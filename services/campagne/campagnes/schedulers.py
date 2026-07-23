"""APScheduler cron du Campagne Service — démarre les campagnes planifiées."""

import logging

from apscheduler.schedulers.background import BackgroundScheduler
from apscheduler.triggers.cron import CronTrigger

logger = logging.getLogger(__name__)

# Verrou consultatif PostgreSQL du cron de démarrage : une seule instance démarre
# les campagnes planifiées, même en cas de réplication (anti double-démarrage).
_CAMPAGNE_LOCK_KEY = 4210002

_scheduler: BackgroundScheduler | None = None


def campagne_planifiee_job() -> None:
    """
    Cron 7h00 : démarre les campagnes dont date_planifiee == aujourd'hui ou J-1.
    Permet de rattraper un démarrage manqué la veille.
    """
    import django

    django.setup()

    from django.db import connection

    from campagnes.grpc_clients import NotificationServiceClient
    from campagnes.services import CampagneService

    # Verrou consultatif PostgreSQL : une seule instance démarre les campagnes
    # planifiées (anti double-démarrage en réplication). SQLite (tests) n'a pas
    # pg_try_advisory_lock → on saute simplement le verrou.
    use_lock = connection.vendor == "postgresql"
    if use_lock:  # pragma: no cover
        with connection.cursor() as cur:
            cur.execute("SELECT pg_try_advisory_lock(%s)", [_CAMPAGNE_LOCK_KEY])
            if not cur.fetchone()[0]:
                logger.info("CampagnePlanifieeJob ignoré — verrou détenu par une autre instance.")
                return

    try:
        demarrees = CampagneService().demarrer_campagnes_planifiees_pour_aujourd_hui()
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
    finally:
        if use_lock:  # pragma: no cover
            with connection.cursor() as cur:
                cur.execute("SELECT pg_advisory_unlock(%s)", [_CAMPAGNE_LOCK_KEY])


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
