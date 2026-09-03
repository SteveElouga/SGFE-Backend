"""APScheduler du Notification Service — traitement des diffusions en fond.

Contrairement aux crons de Paiement/Campagne (une passe par jour, à heure
fixe), ce job tourne en continu par petits lots : une diffusion vise
potentiellement des dizaines d'abonnés, et les envoyer tous d'un coup
ressemblerait à du spam sur le compte WhatsApp Web partagé par tout le
système. `IntervalTrigger`, pas `CronTrigger`.
"""

import logging

from apscheduler.schedulers.background import BackgroundScheduler
from apscheduler.triggers.interval import IntervalTrigger

logger = logging.getLogger(__name__)

# Verrou consultatif PostgreSQL : une seule instance traite un lot à la fois,
# même en cas de réplication (anti double-envoi du même lot).
_DIFFUSION_LOCK_KEY = 4210002

# 5 messages toutes les 15 secondes ≈ 20/minute — throttle délibéré, pas une
# limite technique de whatsapp-service.
_TAILLE_LOT = 5

_scheduler: BackgroundScheduler | None = None


def diffusion_processor_job() -> None:  # pragma: no cover
    """Envoie un lot de messages de diffusion en attente, et referme les
    diffusions dont tous les envois sont résolus (réussis ou en échec)."""
    import django

    django.setup()

    from django.db import connection

    from notifications.event_publisher import publish_diffusion_event
    from notifications.services import DiffusionService

    use_lock = connection.vendor == "postgresql"
    if use_lock:
        with connection.cursor() as cur:
            cur.execute("SELECT pg_try_advisory_lock(%s)", [_DIFFUSION_LOCK_KEY])
            if not cur.fetchone()[0]:
                logger.info("DiffusionProcessorJob ignoré — verrou détenu par une autre instance.")
                return

    try:
        diffusion_ids = DiffusionService().traiter_lot_en_attente(_TAILLE_LOT)
        for diffusion_id in diffusion_ids:
            publish_diffusion_event(diffusion_id)
    except Exception as exc:
        logger.exception("DiffusionProcessorJob échoué : %s", exc)
    finally:
        if use_lock:
            with connection.cursor() as cur:
                cur.execute("SELECT pg_advisory_unlock(%s)", [_DIFFUSION_LOCK_KEY])


def start_scheduler() -> None:
    """Démarre le scheduler APScheduler en arrière-plan."""
    global _scheduler
    if _scheduler and _scheduler.running:
        return

    _scheduler = BackgroundScheduler()
    _scheduler.add_job(
        diffusion_processor_job,
        trigger=IntervalTrigger(seconds=15),
        id="diffusion_processor",
        coalesce=True,
        replace_existing=True,
    )
    _scheduler.start()
    logger.info("NotificationScheduler démarré — diffusions traitées toutes les 15s.")
