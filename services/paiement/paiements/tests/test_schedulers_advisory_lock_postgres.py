"""Comportement RÉEL du verrou consultatif PostgreSQL du cron impayés
(`schedulers.py::impaye_checker_job`).

Aucun test de ce dépôt n'exerçait jusqu'ici `impaye_checker_job` lui-même
(ni son verrou) — seule la logique métier qu'il appelle
(`ImpayeService.verifier_et_escalader`) est couverte ailleurs. Le verrou
consultatif (`pg_try_advisory_lock`/`pg_advisory_unlock`, anti
relances/suspensions dupliquées en cas de réplication) n'a de sens que
contre un vrai second détenteur de session Postgres — jamais démontré nulle
part avec une connexion réellement distincte.

Gaté par `FORCE_POSTGRES_TESTS` : no-op sur SQLite. `TransactionTestCase`
(pas `TestCase`) : le test tient une seconde connexion Postgres RÉELLE
ouverte pendant l'assertion, ce que l'isolation transactionnelle de
`TestCase` ne permet pas d'exercer proprement (voir le même commentaire dans
`services/notification/notifications/tests/test_schedulers_advisory_lock_postgres.py`,
même patron appliqué ici).
"""

from __future__ import annotations

from unittest import skipUnless
from unittest.mock import patch

import psycopg2  # type: ignore[import-untyped]  # pas de stubs dédiés — voir mypy.ini du dépôt
from django.conf import settings
from django.db import connection
from django.test import TransactionTestCase

from paiements.schedulers import _IMPAYE_LOCK_KEY, impaye_checker_job
from paiements.services import ImpayeService

_SUR_POSTGRESQL = connection.vendor == "postgresql"
_RAISON_SKIP = "nécessite un vrai Postgres (FORCE_POSTGRES_TESTS=True) — no-op sur SQLite"


def _connexion_brute_pg() -> "psycopg2.extensions.connection":
    """Ouvre une VRAIE seconde connexion Postgres, indépendante de celle du
    test — c'est elle qui détient le verrou consultatif pendant qu'on
    vérifie que le job (sa propre connexion) ne peut pas l'obtenir."""
    db = settings.DATABASES["default"]
    return psycopg2.connect(
        host=db["HOST"],
        port=db["PORT"],
        dbname=db["NAME"],
        user=db["USER"],
        password=db["PASSWORD"],
    )


@skipUnless(_SUR_POSTGRESQL, _RAISON_SKIP)
class ImpayeCheckerJobVerrouReelTests(TransactionTestCase):
    def setUp(self) -> None:
        self.autre_session = _connexion_brute_pg()

    def tearDown(self) -> None:
        self.autre_session.close()

    def test_le_job_n_escalade_rien_si_le_verrou_est_deja_tenu(self) -> None:
        """Une autre session tient RÉELLEMENT `pg_try_advisory_lock` sur
        `_IMPAYE_LOCK_KEY` : le job doit renoncer avant d'appeler
        `ImpayeService.verifier_et_escalader` — jamais une double escalade
        (relances/suspensions dupliquées) en cas de réplication."""
        with self.autre_session.cursor() as cur:
            cur.execute("SELECT pg_try_advisory_lock(%s)", (_IMPAYE_LOCK_KEY,))
            (obtenu,) = cur.fetchone()
        self.assertTrue(obtenu, "précondition : l'autre session doit avoir obtenu le verrou")

        with patch.object(ImpayeService, "verifier_et_escalader") as mock_verifier:
            impaye_checker_job()
            mock_verifier.assert_not_called()

        with self.autre_session.cursor() as cur:
            cur.execute("SELECT pg_advisory_unlock(%s)", (_IMPAYE_LOCK_KEY,))
            (libere,) = cur.fetchone()
        self.assertTrue(libere)

    def test_le_job_escalade_puis_libere_reellement_le_verrou(self) -> None:
        """Verrou libre : le job l'obtient, appelle le service, puis le
        relâche pour de vrai — vérifié en l'acquérant depuis une AUTRE
        session immédiatement après (le cron du lendemain doit pouvoir
        l'obtenir sans attendre un redémarrage du process)."""
        with patch.object(ImpayeService, "verifier_et_escalader") as mock_verifier:
            impaye_checker_job()
            mock_verifier.assert_called_once()

        with self.autre_session.cursor() as cur:
            cur.execute("SELECT pg_try_advisory_lock(%s)", (_IMPAYE_LOCK_KEY,))
            (obtenu_apres_coup,) = cur.fetchone()
        self.assertTrue(obtenu_apres_coup, "le job doit avoir relâché le verrou en sortie de bloc `finally`")
        with self.autre_session.cursor() as cur:
            cur.execute("SELECT pg_advisory_unlock(%s)", (_IMPAYE_LOCK_KEY,))

    def test_le_verrou_est_libere_meme_si_le_service_leve(self) -> None:
        """Le `finally` de `impaye_checker_job` doit libérer le verrou même
        en cas d'exception dans la logique métier — sinon un incident isolé
        bloquerait tous les jours suivants jusqu'à un redémarrage."""
        with patch.object(ImpayeService, "verifier_et_escalader", side_effect=RuntimeError("boom")):
            impaye_checker_job()  # ne doit pas lever — l'erreur est journalisée

        with self.autre_session.cursor() as cur:
            cur.execute("SELECT pg_try_advisory_lock(%s)", (_IMPAYE_LOCK_KEY,))
            (obtenu_apres_coup,) = cur.fetchone()
        self.assertTrue(obtenu_apres_coup, "le verrou doit être libéré même après une exception métier")
        with self.autre_session.cursor() as cur:
            cur.execute("SELECT pg_advisory_unlock(%s)", (_IMPAYE_LOCK_KEY,))
