"""Tests du scheduler de l'Auth Service (comptes/schedulers.py).

Couvre `purge_rgpd_job` : appel du service sur SQLite (verrou sauté, même
bypass que les autres schedulers du dépôt — voir
`stats/tests/test_schedulers.py`), respect du verrou consultatif PostgreSQL
simulé, et enregistrement du job dans `start_scheduler`.
"""

from unittest.mock import MagicMock, patch

from apscheduler.triggers.cron import CronTrigger
from django.test import TestCase

from comptes.services import UserAdminService


class PurgeRgpdJobTests(TestCase):
    """SQLite (tests) n'a pas pg_try_advisory_lock : le job saute le verrou et
    s'exécute directement (même bypass que les autres jobs du dépôt)."""

    @patch.object(UserAdminService, "purger_utilisateurs_desactives", return_value=(3, 1))
    def test_job_appelle_le_service(self, mock_purger: MagicMock) -> None:
        from comptes.schedulers import purge_rgpd_job

        purge_rgpd_job()  # ne doit lever aucune exception

        mock_purger.assert_called_once()

    @patch.object(UserAdminService, "purger_utilisateurs_desactives", side_effect=RuntimeError("boom"))
    def test_job_ne_propage_pas_les_erreurs(self, mock_purger: MagicMock) -> None:
        """Un job de fond ne doit jamais planter le process gRPC qui l'héberge."""
        from comptes.schedulers import purge_rgpd_job

        purge_rgpd_job()  # ne doit pas lever, l'erreur est journalisée

    @patch.object(UserAdminService, "purger_utilisateurs_desactives")
    @patch("django.db.connection")
    def test_verrou_deja_pris_empeche_le_traitement(self, mock_connection: MagicMock, mock_purger: MagicMock) -> None:
        """Si `pg_try_advisory_lock` échoue (verrou détenu par une autre
        instance), le job ne doit pas purger — simulé en forçant
        `connection.vendor` à 'postgresql' (bypassé sur SQLite sinon)."""
        mock_connection.vendor = "postgresql"
        mock_cursor = MagicMock()
        mock_cursor.fetchone.return_value = (False,)  # verrou NON obtenu
        mock_connection.cursor.return_value.__enter__.return_value = mock_cursor

        from comptes.schedulers import purge_rgpd_job

        purge_rgpd_job()

        mock_purger.assert_not_called()

    @patch.object(UserAdminService, "purger_utilisateurs_desactives", return_value=(0, 0))
    @patch("django.db.connection")
    def test_verrou_obtenu_traite_puis_libere(self, mock_connection: MagicMock, mock_purger: MagicMock) -> None:
        """Verrou obtenu : le service est appelé, puis le verrou est libéré."""
        mock_connection.vendor = "postgresql"
        mock_cursor = MagicMock()
        mock_cursor.fetchone.return_value = (True,)  # verrou obtenu
        mock_connection.cursor.return_value.__enter__.return_value = mock_cursor

        from comptes.schedulers import _PURGE_RGPD_LOCK_KEY, purge_rgpd_job

        purge_rgpd_job()

        mock_purger.assert_called_once()
        # SELECT pg_try_advisory_lock puis SELECT pg_advisory_unlock, la même clé.
        appels = [c.args[1] for c in mock_cursor.execute.call_args_list]
        self.assertEqual(appels, [[_PURGE_RGPD_LOCK_KEY], [_PURGE_RGPD_LOCK_KEY]])


class StartSchedulerTests(TestCase):
    def tearDown(self) -> None:
        from comptes import schedulers

        schedulers.stop_scheduler()
        schedulers._scheduler = None

    def test_start_scheduler_enregistre_le_job_quotidien(self) -> None:
        from comptes.schedulers import start_scheduler

        start_scheduler()

        from comptes import schedulers

        assert schedulers._scheduler is not None
        job = schedulers._scheduler.get_job("purge_rgpd")
        self.assertIsNotNone(job)
        self.assertIsInstance(job.trigger, CronTrigger)

    def test_start_scheduler_idempotent(self) -> None:
        from comptes.schedulers import start_scheduler

        start_scheduler()
        start_scheduler()  # ne doit pas lever ni dupliquer le scheduler

        from comptes import schedulers

        assert schedulers._scheduler is not None
        self.assertTrue(schedulers._scheduler.running)
