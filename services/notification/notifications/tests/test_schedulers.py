"""Tests du scheduler du Notification Service (notifications/schedulers.py).

Couvre `retry_envois_echec_job` : appel du service sur SQLite (verrou
sauté, même bypass que les autres schedulers du dépôt — voir
`stats/tests/test_schedulers.py`), respect du verrou consultatif PostgreSQL
simulé, et enregistrement du job dans `start_scheduler`.
"""

from unittest.mock import MagicMock, patch

from apscheduler.triggers.interval import IntervalTrigger
from django.test import TestCase

from notifications.services import EnvoiService


class RetryEnvoisEchecJobTests(TestCase):
    """SQLite (tests) n'a pas pg_try_advisory_lock : le job saute le verrou et
    s'exécute directement (même bypass que les autres jobs du dépôt)."""

    @patch.object(EnvoiService, "retenter_echecs", return_value=[])
    def test_job_appelle_le_service(self, mock_retenter: MagicMock) -> None:
        from notifications.schedulers import _TAILLE_LOT_RETRY, retry_envois_echec_job

        retry_envois_echec_job()  # ne doit lever aucune exception

        mock_retenter.assert_called_once_with(_TAILLE_LOT_RETRY)

    @patch.object(EnvoiService, "retenter_echecs", side_effect=RuntimeError("boom"))
    def test_job_ne_propage_pas_les_erreurs(self, mock_retenter: MagicMock) -> None:
        """Un job de fond ne doit jamais planter le process gRPC qui l'héberge."""
        from notifications.schedulers import retry_envois_echec_job

        retry_envois_echec_job()  # ne doit pas lever, l'erreur est journalisée

    @patch.object(EnvoiService, "retenter_echecs")
    @patch("django.db.connection")
    def test_verrou_deja_pris_empeche_le_traitement(self, mock_connection: MagicMock, mock_retenter: MagicMock) -> None:
        """Si `pg_try_advisory_lock` échoue (verrou détenu par une autre
        instance), le job ne doit pas retenter les échecs — simulé en forçant
        `connection.vendor` à 'postgresql' (bypassé sur SQLite sinon)."""
        mock_connection.vendor = "postgresql"
        mock_cursor = MagicMock()
        mock_cursor.fetchone.return_value = (False,)  # verrou NON obtenu
        mock_connection.cursor.return_value.__enter__.return_value = mock_cursor

        from notifications.schedulers import retry_envois_echec_job

        retry_envois_echec_job()

        mock_retenter.assert_not_called()

    @patch.object(EnvoiService, "retenter_echecs", return_value=[])
    @patch("django.db.connection")
    def test_verrou_obtenu_traite_puis_libere(self, mock_connection: MagicMock, mock_retenter: MagicMock) -> None:
        """Verrou obtenu : le service est appelé, puis le verrou est libéré."""
        mock_connection.vendor = "postgresql"
        mock_cursor = MagicMock()
        mock_cursor.fetchone.return_value = (True,)  # verrou obtenu
        mock_connection.cursor.return_value.__enter__.return_value = mock_cursor

        from notifications.schedulers import _RETRY_ENVOIS_LOCK_KEY, retry_envois_echec_job

        retry_envois_echec_job()

        mock_retenter.assert_called_once()
        # SELECT pg_try_advisory_lock puis SELECT pg_advisory_unlock, la même clé.
        appels = [c.args[1] for c in mock_cursor.execute.call_args_list]
        self.assertEqual(appels, [[_RETRY_ENVOIS_LOCK_KEY], [_RETRY_ENVOIS_LOCK_KEY]])


class StartSchedulerTests(TestCase):
    def tearDown(self) -> None:
        from notifications import schedulers

        if schedulers._scheduler is not None:
            schedulers._scheduler.shutdown(wait=False)
        schedulers._scheduler = None

    def test_start_scheduler_enregistre_les_deux_jobs(self) -> None:
        from notifications.schedulers import start_scheduler

        start_scheduler()

        from notifications import schedulers

        assert schedulers._scheduler is not None
        job_diffusion = schedulers._scheduler.get_job("diffusion_processor")
        job_retry = schedulers._scheduler.get_job("retry_envois_echec")
        self.assertIsNotNone(job_diffusion)
        self.assertIsNotNone(job_retry)
        assert job_retry is not None
        self.assertIsInstance(job_retry.trigger, IntervalTrigger)

    def test_start_scheduler_idempotent(self) -> None:
        from notifications.schedulers import start_scheduler

        start_scheduler()
        start_scheduler()  # ne doit pas lever ni dupliquer le scheduler

        from notifications import schedulers

        assert schedulers._scheduler is not None
        self.assertTrue(schedulers._scheduler.running)
