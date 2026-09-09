"""Comportement RÉEL du verrou consultatif PostgreSQL du cron de démarrage
des campagnes planifiées (`schedulers.py::campagne_planifiee_job`).

Aucun test de ce dépôt n'exerçait jusqu'ici `campagne_planifiee_job` lui-même
(ni son verrou) — seule la logique métier qu'il appelle
(`CampagneService.demarrer_campagnes_planifiees_pour_aujourd_hui`) est
couverte ailleurs. Le verrou consultatif (`pg_try_advisory_lock`/
`pg_advisory_unlock`, anti double-démarrage en cas de réplication) n'a de
sens que contre un vrai second détenteur de session Postgres.

Gaté par `FORCE_POSTGRES_TESTS` : no-op sur SQLite. `TransactionTestCase`
(pas `TestCase`) : le test tient une seconde connexion Postgres RÉELLE
ouverte pendant l'assertion — même patron que
`services/paiement/paiements/tests/test_schedulers_advisory_lock_postgres.py`
et `services/notification/notifications/tests/test_schedulers_advisory_lock_postgres.py`.
"""

from __future__ import annotations

from unittest import skipUnless
from unittest.mock import patch

import psycopg2  # type: ignore[import-untyped]  # pas de stubs dédiés — voir mypy.ini du dépôt
from django.conf import settings
from django.db import connection
from django.test import TransactionTestCase

from campagnes.schedulers import _CAMPAGNE_LOCK_KEY, campagne_planifiee_job
from campagnes.services import CampagneService

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
class CampagnePlanifieeJobVerrouReelTests(TransactionTestCase):
    def setUp(self) -> None:
        self.autre_session = _connexion_brute_pg()

    def tearDown(self) -> None:
        self.autre_session.close()

    def test_le_job_ne_demarre_rien_si_le_verrou_est_deja_tenu(self) -> None:
        """Une autre session tient RÉELLEMENT `pg_try_advisory_lock` sur
        `_CAMPAGNE_LOCK_KEY` : le job doit renoncer avant d'appeler
        `CampagneService.demarrer_campagnes_planifiees_pour_aujourd_hui` —
        jamais un double démarrage de la même campagne en réplication."""
        with self.autre_session.cursor() as cur:
            cur.execute("SELECT pg_try_advisory_lock(%s)", (_CAMPAGNE_LOCK_KEY,))
            (obtenu,) = cur.fetchone()
        self.assertTrue(obtenu, "précondition : l'autre session doit avoir obtenu le verrou")

        with patch.object(CampagneService, "demarrer_campagnes_planifiees_pour_aujourd_hui") as mock_demarrer:
            campagne_planifiee_job()
            mock_demarrer.assert_not_called()

        with self.autre_session.cursor() as cur:
            cur.execute("SELECT pg_advisory_unlock(%s)", (_CAMPAGNE_LOCK_KEY,))
            (libere,) = cur.fetchone()
        self.assertTrue(libere)

    def test_le_job_demarre_puis_libere_reellement_le_verrou(self) -> None:
        """Verrou libre : le job l'obtient, appelle le service, puis le
        relâche pour de vrai — vérifié en l'acquérant depuis une AUTRE
        session immédiatement après (le cron du lendemain doit pouvoir
        l'obtenir sans attendre un redémarrage du process)."""
        with patch.object(
            CampagneService, "demarrer_campagnes_planifiees_pour_aujourd_hui", return_value=[]
        ) as mock_demarrer:
            campagne_planifiee_job()
            mock_demarrer.assert_called_once()

        with self.autre_session.cursor() as cur:
            cur.execute("SELECT pg_try_advisory_lock(%s)", (_CAMPAGNE_LOCK_KEY,))
            (obtenu_apres_coup,) = cur.fetchone()
        self.assertTrue(obtenu_apres_coup, "le job doit avoir relâché le verrou en sortie de bloc `finally`")
        with self.autre_session.cursor() as cur:
            cur.execute("SELECT pg_advisory_unlock(%s)", (_CAMPAGNE_LOCK_KEY,))

    def test_le_verrou_est_libere_meme_si_le_service_leve(self) -> None:
        """`campagne_planifiee_job` (contrairement à `facturation_retry_job`
        du même fichier) ne capture PAS les exceptions de la logique
        métier — l'exception doit donc bien se propager, mais le `finally`
        doit malgré tout libérer le verrou : sinon un incident isolé
        bloquerait le cron de tous les jours suivants jusqu'à un
        redémarrage du process."""
        with patch.object(
            CampagneService,
            "demarrer_campagnes_planifiees_pour_aujourd_hui",
            side_effect=RuntimeError("boom"),
        ):
            with self.assertRaises(RuntimeError):
                campagne_planifiee_job()

        with self.autre_session.cursor() as cur:
            cur.execute("SELECT pg_try_advisory_lock(%s)", (_CAMPAGNE_LOCK_KEY,))
            (obtenu_apres_coup,) = cur.fetchone()
        self.assertTrue(obtenu_apres_coup, "le verrou doit être libéré même après une exception métier")
        with self.autre_session.cursor() as cur:
            cur.execute("SELECT pg_advisory_unlock(%s)", (_CAMPAGNE_LOCK_KEY,))
