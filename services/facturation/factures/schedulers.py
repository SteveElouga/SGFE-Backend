"""APScheduler du Facturation Service — relais de l'outbox transactionnelle.

Contrairement aux crons de Paiement/Campagne (une passe par jour, à heure
fixe), ce job tourne en continu par petits lots — même patron que
`notifications/schedulers.py::diffusion_processor_job` (`IntervalTrigger`,
pas `CronTrigger`) : un événement `FACTURE_GENEREE` en attente doit atteindre
Paiement Service en quelques secondes, pas au prochain jour ouvré, sinon
l'abonné qui règle sa facture dans la foulée de sa réception se heurterait à
un solde absent.

Voir `factures/services.py::OutboxRelayService` pour la logique métier ;
`factures/models.py::OutboxEvent` pour le contrat de la table.
"""

import logging

from apscheduler.schedulers.background import BackgroundScheduler
from apscheduler.triggers.interval import IntervalTrigger

logger = logging.getLogger(__name__)

# Verrou consultatif PostgreSQL du relais outbox : une seule instance relaie à
# la fois, même en cas de réplication (anti double-appel concurrent du même
# événement). Distinct de `_IMPAYE_LOCK_KEY` (paiement, 4210001),
# `_DIFFUSION_LOCK_KEY`/`_CAMPAGNE_LOCK_KEY` (notification/campagne, 4210002),
# `_FACTURATION_RETRY_LOCK_KEY` (campagne, 4210003) et
# `_RECONCILIATION_LOCK_KEY` (reporting, 4210004) — sans effet pratique ici
# (chaque service a sa propre instance PostgreSQL, voir docker-compose.yml),
# mais garder les clés distinctes évite toute ambiguïté si ce registre de
# verrous devait un jour être relu globalement (voir CLAUDE.md racine).
_OUTBOX_RELAY_LOCK_KEY = 4210005

# Un lot modeste : le relais repasse toutes les 10 secondes, un gros lot ne
# ferait qu'allonger inutilement chaque transaction de lecture.
_TAILLE_LOT = 100

_scheduler: BackgroundScheduler | None = None


def outbox_relay_job() -> None:  # pragma: no cover - couvert via OutboxRelayService directement
    """Relaie un lot d'événements outbox `EN_ATTENTE` vers Paiement Service.

    Verrou consultatif PostgreSQL : une seule instance relaie à la fois
    (anti double-appel en réplication). SQLite (tests) n'a pas
    `pg_try_advisory_lock` → on saute simplement le verrou.
    """
    import django

    django.setup()

    from django.db import connection

    from factures.services import OutboxRelayService

    use_lock = connection.vendor == "postgresql"
    if use_lock:
        with connection.cursor() as cur:
            cur.execute("SELECT pg_try_advisory_lock(%s)", [_OUTBOX_RELAY_LOCK_KEY])
            if not cur.fetchone()[0]:
                logger.info("OutboxRelayJob ignoré — verrou détenu par une autre instance.")
                return

    try:
        envoyes, echoues, abandonnes = OutboxRelayService().relayer_lot(limit=_TAILLE_LOT)
        if envoyes or echoues or abandonnes:
            logger.info(
                "OutboxRelayJob terminé : %s envoyé(s), %s échec(s) temporaire(s), %s abandonné(s).",
                envoyes,
                echoues,
                abandonnes,
            )
    except Exception as exc:
        logger.exception("OutboxRelayJob échoué : %s", exc)
    finally:
        if use_lock:
            with connection.cursor() as cur:
                cur.execute("SELECT pg_advisory_unlock(%s)", [_OUTBOX_RELAY_LOCK_KEY])


def start_scheduler() -> None:
    """Démarre le scheduler APScheduler en arrière-plan."""
    global _scheduler
    if _scheduler and _scheduler.running:
        return

    _scheduler = BackgroundScheduler()
    _scheduler.add_job(
        outbox_relay_job,
        trigger=IntervalTrigger(seconds=10),
        id="outbox_relay",
        coalesce=True,
        replace_existing=True,
    )
    _scheduler.start()
    logger.info("FacturationScheduler démarré — relais outbox toutes les 10s.")


def stop_scheduler() -> None:
    """Arrête proprement le scheduler."""
    global _scheduler
    if _scheduler and _scheduler.running:
        _scheduler.shutdown(wait=False)
        logger.info("FacturationScheduler arrêté.")
