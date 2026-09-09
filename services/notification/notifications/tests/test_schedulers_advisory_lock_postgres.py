"""Comportement RÉEL du verrou consultatif PostgreSQL des jobs de fond
(`schedulers.py::diffusion_processor_job` / `retry_envois_echec_job`).

`tests/test_schedulers.py` (existant) simule `pg_try_advisory_lock` en
mockant entièrement `django.db.connection` — utile pour le chemin logique
(verrou obtenu/refusé), mais ça ne prouve jamais qu'un VRAI second
appelant, avec sa PROPRE connexion Postgres, est effectivement bloqué :
`pg_try_advisory_lock` n'a de sens que contre un vrai moteur qui tient l'état
du verrou côté serveur, pas contre une session simulée.

Gaté par `FORCE_POSTGRES_TESTS` (comme les jobs CI notification) : no-op
sur SQLite, qui n'a pas de verrous consultatifs. `TransactionTestCase` est
nécessaire ici (pas `TestCase`) : le test tient volontairement une seconde
connexion Postgres RÉELLE et distincte ouverte pendant l'assertion — sous
`TestCase`, toute la suite tournerait dans une unique transaction non
committée, invisible aux autres connexions/verrous par nature transactionnels
au sens Postgres (un verrou de session survit lui à la transaction, mais le
point ici est justement d'observer DEUX connexions distinctes en même temps,
ce que l'isolation de `TestCase` ne permet pas d'exercer proprement).
"""

from __future__ import annotations

import uuid
from unittest import skipUnless
from unittest.mock import patch

import psycopg2  # type: ignore[import-untyped]  # pas de stubs dédiés — voir mypy.ini du dépôt
from django.conf import settings
from django.db import connection
from django.test import TransactionTestCase

from notifications.models import Diffusion, DiffusionEnvoi, Envoi, StatutEnvoi, TypeEnvoi
from notifications.schedulers import (
    _DIFFUSION_LOCK_KEY,
    _RETRY_ENVOIS_LOCK_KEY,
    diffusion_processor_job,
    retry_envois_echec_job,
)
from notifications.services import EnvoiService

_SUR_POSTGRESQL = connection.vendor == "postgresql"
_RAISON_SKIP = "nécessite un vrai Postgres (FORCE_POSTGRES_TESTS=True) — no-op sur SQLite"


def _connexion_brute_pg() -> "psycopg2.extensions.connection":
    """Ouvre une VRAIE seconde connexion Postgres, indépendante de celle de
    Django/du test — c'est elle qui détient le verrou consultatif pendant
    qu'on vérifie qu'une autre session (celle du job) ne peut pas l'obtenir."""
    db = settings.DATABASES["default"]
    return psycopg2.connect(
        host=db["HOST"],
        port=db["PORT"],
        dbname=db["NAME"],
        user=db["USER"],
        password=db["PASSWORD"],
    )


@skipUnless(_SUR_POSTGRESQL, _RAISON_SKIP)
class DiffusionProcessorJobVerrouReelTests(TransactionTestCase):
    """`_DIFFUSION_LOCK_KEY` contre un vrai second détenteur de session."""

    def setUp(self) -> None:
        self.autre_session = _connexion_brute_pg()

    def tearDown(self) -> None:
        self.autre_session.close()

    def test_le_job_ignore_le_lot_si_le_verrou_est_deja_tenu(self) -> None:
        """Une autre session tient RÉELLEMENT `pg_try_advisory_lock` sur la
        même clé : le job ne doit traiter aucune ligne, et ne doit PAS relâcher
        un verrou qu'il n'a jamais obtenu."""
        diffusion = Diffusion.objects.create(message="Annonce")
        DiffusionEnvoi.objects.create(diffusion=diffusion, abonne_id="a1", telephone="+237699000001")

        with self.autre_session.cursor() as cur:
            cur.execute("SELECT pg_try_advisory_lock(%s)", (_DIFFUSION_LOCK_KEY,))
            (obtenu,) = cur.fetchone()
        self.assertTrue(obtenu, "précondition : l'autre session doit avoir obtenu le verrou")

        with patch("notifications.services.whatsapp_client") as mock_wa:
            mock_wa.send.return_value = None
            diffusion_processor_job()
            mock_wa.send.assert_not_called()

        # La ligne n'a pas bougé : le job a bien renoncé avant tout traitement.
        envoi = DiffusionEnvoi.objects.get(diffusion=diffusion)
        self.assertEqual(envoi.statut, "EN_ATTENTE")

        # Le verrou est resté à l'autre session — la libérer ici doit réussir.
        with self.autre_session.cursor() as cur:
            cur.execute("SELECT pg_advisory_unlock(%s)", (_DIFFUSION_LOCK_KEY,))
            (libere,) = cur.fetchone()
        self.assertTrue(libere)

    def test_le_job_traite_le_lot_et_libere_reellement_le_verrou(self) -> None:
        """Verrou libre : le job l'obtient, traite, puis le relâche pour de
        vrai — vérifié en tentant de l'acquérir depuis une AUTRE session
        immédiatement après (`consumer` réutilisable au tour suivant)."""
        diffusion = Diffusion.objects.create(message="Annonce")
        DiffusionEnvoi.objects.create(diffusion=diffusion, abonne_id="a1", telephone="+237699000001")

        with (
            patch("notifications.services.whatsapp_client") as mock_wa,
            patch("notifications.event_publisher.publish_diffusion_event"),
        ):
            mock_wa.send.return_value = None
            diffusion_processor_job()
            mock_wa.send.assert_called_once()

        with self.autre_session.cursor() as cur:
            cur.execute("SELECT pg_try_advisory_lock(%s)", (_DIFFUSION_LOCK_KEY,))
            (obtenu_apres_coup,) = cur.fetchone()
        self.assertTrue(obtenu_apres_coup, "le job doit avoir relâché le verrou en sortie de bloc `finally`")
        with self.autre_session.cursor() as cur:
            cur.execute("SELECT pg_advisory_unlock(%s)", (_DIFFUSION_LOCK_KEY,))


@skipUnless(_SUR_POSTGRESQL, _RAISON_SKIP)
class RetryEnvoisEchecJobVerrouReelTests(TransactionTestCase):
    """`_RETRY_ENVOIS_LOCK_KEY` contre un vrai second détenteur de session —
    même principe que ci-dessus, pour le second job de fond du service."""

    def setUp(self) -> None:
        self.autre_session = _connexion_brute_pg()

    def tearDown(self) -> None:
        self.autre_session.close()

    def test_le_job_ignore_le_lot_si_le_verrou_est_deja_tenu(self) -> None:
        Envoi.objects.create(
            facture_id=str(uuid.uuid4()),
            abonne_id=str(uuid.uuid4()),
            type_envoi=TypeEnvoi.FACTURE,
            telephone="+237699000001",
            statut=StatutEnvoi.ECHEC,
            dernier_message="Votre facture...",
        )

        with self.autre_session.cursor() as cur:
            cur.execute("SELECT pg_try_advisory_lock(%s)", (_RETRY_ENVOIS_LOCK_KEY,))
            (obtenu,) = cur.fetchone()
        self.assertTrue(obtenu)

        with patch.object(EnvoiService, "retenter_echecs") as mock_retenter:
            retry_envois_echec_job()
            mock_retenter.assert_not_called()

        with self.autre_session.cursor() as cur:
            cur.execute("SELECT pg_advisory_unlock(%s)", (_RETRY_ENVOIS_LOCK_KEY,))

    def test_le_job_traite_puis_libere_reellement_le_verrou(self) -> None:
        with patch.object(EnvoiService, "retenter_echecs", return_value=[]) as mock_retenter:
            retry_envois_echec_job()
            mock_retenter.assert_called_once()

        with self.autre_session.cursor() as cur:
            cur.execute("SELECT pg_try_advisory_lock(%s)", (_RETRY_ENVOIS_LOCK_KEY,))
            (obtenu_apres_coup,) = cur.fetchone()
        self.assertTrue(obtenu_apres_coup, "le job doit avoir relâché son verrou en sortie de bloc `finally`")
        with self.autre_session.cursor() as cur:
            cur.execute("SELECT pg_advisory_unlock(%s)", (_RETRY_ENVOIS_LOCK_KEY,))

    def test_les_deux_jobs_ont_des_clefs_de_verrou_distinctes(self) -> None:
        """Filet de non-régression direct : si un jour quelqu'un fusionne les
        deux constantes par erreur, ce test le détecte sans avoir besoin de
        Postgres — mais il vit ici pour rester à côté des tests qui exercent
        vraiment les deux verrous."""
        self.assertNotEqual(_DIFFUSION_LOCK_KEY, _RETRY_ENVOIS_LOCK_KEY)
