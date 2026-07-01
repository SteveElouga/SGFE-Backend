"""APScheduler cron du Campagne Service — démarre les campagnes planifiées."""

import logging

from apscheduler.schedulers.background import BackgroundScheduler
from apscheduler.triggers.cron import CronTrigger

logger = logging.getLogger(__name__)

_scheduler: BackgroundScheduler | None = None


def campagne_planifiee_job() -> None:
    """
    Cron 7h00 : démarre les campagnes dont date_planifiee == aujourd'hui ou J-1.
    Permet de rattraper un démarrage manqué la veille.
    """
    import django

    django.setup()

    from campagnes.grpc_clients import NotificationServiceClient
    from campagnes.services import CampagneService

    svc = CampagneService()
    demarrees = svc.demarrer_campagnes_planifiees_pour_aujourd_hui()
    if demarrees:
        notif_client = NotificationServiceClient()
        for c in demarrees:
            logger.info(
                "Campagne démarrée automatiquement",
                extra={"campagne_id": str(c.id), "nom": c.nom},
            )
            # EF-NOTIF-005 — Notifier les admins du démarrage
            notif_client.notifier_admins(
                evenement="CAMPAGNE_PLANIFIEE",
                detail=f"Campagne « {c.nom} » démarrée automatiquement",
                entite_id=str(c.id),
            )
    else:
        logger.debug("Aucune campagne planifiée à démarrer aujourd'hui.")


def start_scheduler() -> None:
    """Démarre le scheduler APScheduler en arrière-plan."""
    global _scheduler
    if _scheduler and _scheduler.running:
        return

    _scheduler = BackgroundScheduler()
    _scheduler.add_job(
        campagne_planifiee_job,
        trigger=CronTrigger(hour=7, minute=0),
        id="campagne_planifiee",
        replace_existing=True,
    )
    _scheduler.start()
    logger.info("CampagneScheduler démarré — cron à 07:00 tous les jours.")


def stop_scheduler() -> None:
    """Arrête proprement le scheduler."""
    global _scheduler
    if _scheduler and _scheduler.running:
        _scheduler.shutdown(wait=False)
        logger.info("CampagneScheduler arrêté.")
