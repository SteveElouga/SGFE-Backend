"""APScheduler du Notification Service — traitement des diffusions en fond et
retry automatique des envois WhatsApp en échec.

Contrairement aux crons de Paiement/Campagne (une passe par jour, à heure
fixe), le job de diffusion tourne en continu par petits lots : une diffusion
vise potentiellement des dizaines d'abonnés, et les envoyer tous d'un coup
ressemblerait à du spam sur le compte WhatsApp Web partagé par tout le
système. `IntervalTrigger`, pas `CronTrigger` — même choix pour le retry
WhatsApp ci-dessous.
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

# Verrou consultatif PostgreSQL du job de retry WhatsApp — distinct de
# `_DIFFUSION_LOCK_KEY` : deux jobs indépendants du même service ne doivent
# pas se bloquer l'un l'autre. Prochaine clé libre du registre du projet —
# voir `services/paiement/paiements/schedulers.py` (4210001),
# `services/campagne/campagnes/schedulers.py` (4210002 et 4210003),
# `services/reporting/stats/schedulers.py` (4210004) et `_DIFFUSION_LOCK_KEY`
# ci-dessus (4210002, partagée sans effet pratique avec le cron campagne —
# chaque service a sa propre instance PostgreSQL, voir docker-compose.yml).
_RETRY_ENVOIS_LOCK_KEY = 4210005

# Un lot modeste : le retry n'a pas vocation à rattraper un incident massif en
# une passe, seulement à rejouer les échecs isolés toutes les 15 minutes.
_TAILLE_LOT_RETRY = 20

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


def retry_envois_echec_job() -> None:
    """Retente automatiquement les envois WhatsApp en ECHEC, tant qu'ils n'ont
    pas dépassé `notifications.models.MAX_TENTATIVES_AUTO` tentatives.

    Rejoue le dernier message tenté (`Envoi.dernier_message`), identique à
    l'original — jamais recalculé, pour ne pas annoncer un montant qui a pu
    changer depuis (nouveau versement, annulation…). Le PDF, lui, n'est pas
    stocké et est régénéré au besoin (`Envoi.avec_pdf`). Voir
    `EnvoiService.retenter_echecs` pour le détail, y compris l'abandon
    définitif au-delà du plafond.

    Cadence de 15 minutes : assez pour laisser une panne transitoire de
    whatsapp-service se résorber, assez rapproché pour qu'un abonné ne
    découvre pas sa facture des heures après sa génération.
    """
    import django

    django.setup()

    from django.db import connection

    from notifications.services import EnvoiService

    # Verrou consultatif PostgreSQL : une seule instance retente un lot à la
    # fois (anti double-retry en réplication). SQLite (tests) n'a pas
    # pg_try_advisory_lock → on saute simplement le verrou.
    use_lock = connection.vendor == "postgresql"
    if use_lock:  # pragma: no cover
        with connection.cursor() as cur:
            cur.execute("SELECT pg_try_advisory_lock(%s)", [_RETRY_ENVOIS_LOCK_KEY])
            if not cur.fetchone()[0]:
                logger.info("RetryEnvoisEchecJob ignoré — verrou détenu par une autre instance.")
                return

    try:
        lot = EnvoiService().retenter_echecs(_TAILLE_LOT_RETRY)
        logger.info("RetryEnvoisEchecJob terminé : %d envoi(s) retenté(s).", len(lot))
    except Exception as exc:
        logger.exception("RetryEnvoisEchecJob échoué : %s", exc)
    finally:
        if use_lock:  # pragma: no cover
            with connection.cursor() as cur:
                cur.execute("SELECT pg_advisory_unlock(%s)", [_RETRY_ENVOIS_LOCK_KEY])


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
    _scheduler.add_job(
        retry_envois_echec_job,
        trigger=IntervalTrigger(minutes=15),
        id="retry_envois_echec",
        coalesce=True,
        replace_existing=True,
    )
    _scheduler.start()
    logger.info("NotificationScheduler démarré — diffusions traitées toutes les 15s, retry WhatsApp toutes les 15 min.")
