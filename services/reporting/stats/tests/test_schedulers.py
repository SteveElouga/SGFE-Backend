"""Tests du scheduler de réconciliation nocturne (stats/schedulers.py)."""

from unittest.mock import patch

from apscheduler.triggers.cron import CronTrigger
from django.test import TestCase

from stats.services import ReconciliateurStats


class ReconciliationJobTests(TestCase):
    """SQLite (tests) n'a pas pg_try_advisory_lock : le job saute le verrou et
    s'exécute directement (même bypass que campagne_planifiee_job)."""

    @patch.object(ReconciliateurStats, "reconcilier_toutes_campagnes", return_value=(3, 1))
    def test_reconciliation_job_appelle_le_reconciliateur(self, mock_reconcilier) -> None:
        from stats.schedulers import reconciliation_job

        reconciliation_job()  # ne doit lever aucune exception

        mock_reconcilier.assert_called_once()

    @patch.object(ReconciliateurStats, "reconcilier_toutes_campagnes", side_effect=RuntimeError("boom"))
    def test_reconciliation_job_ne_propage_pas_les_erreurs(self, mock_reconcilier) -> None:
        """Un job de fond ne doit jamais planter le process gRPC qui l'héberge."""
        from stats.schedulers import reconciliation_job

        reconciliation_job()  # ne doit pas lever, l'erreur est journalisée


class StartSchedulerTests(TestCase):
    def tearDown(self) -> None:
        from stats import schedulers

        schedulers.stop_scheduler()
        schedulers._scheduler = None

    def test_start_scheduler_enregistre_le_job_nocturne(self) -> None:
        from stats.schedulers import start_scheduler

        start_scheduler()

        from stats import schedulers

        job = schedulers._scheduler.get_job("reporting_reconciliation")
        self.assertIsNotNone(job)
        self.assertIsInstance(job.trigger, CronTrigger)

    def test_start_scheduler_idempotent(self) -> None:
        from stats.schedulers import start_scheduler

        start_scheduler()
        start_scheduler()  # ne doit pas lever ni dupliquer le scheduler

        from stats import schedulers

        self.assertTrue(schedulers._scheduler.running)
