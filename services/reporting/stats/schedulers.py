"""APScheduler du Reporting Service — réconciliation nocturne des stats.

`event_consumer.py` alimente le read model en continu (Redis Streams,
at-least-once, idempotent via `ProcessedEvent`) mais ne rattrape qu'un
événement publié-puis-perdu — jamais un événement jamais publié (service
producteur tombé avant le XADD). Ce job relit périodiquement Facturation et
Paiement Service (sources de vérité) pour corriger la dérive que ce trou
laisserait sur StatsFacturation/StatsPaiements (compteurs à delta). Voir
`stats/services.py::ReconciliateurStats` pour le détail du calcul.

Même patron que `paiements/schedulers.py` (`impaye_checker_job`) : CronTrigger
nocturne + verrou consultatif PostgreSQL (bypass SQLite en tests).
"""

import logging

from apscheduler.schedulers.background import BackgroundScheduler
from apscheduler.triggers.cron import CronTrigger

logger = logging.getLogger(__name__)

# Verrou consultatif PostgreSQL du job de réconciliation : une seule instance
# réconcilie à la fois, même en cas de réplication (anti double-calcul
# concurrent des mêmes lignes de stats). Distinct de `_IMPAYE_LOCK_KEY`
# (paiement, 4210001) et `_DIFFUSION_LOCK_KEY` (notification, 4210002) — sans
# effet pratique ici (chaque service a sa propre instance PostgreSQL, voir
# docker-compose.yml), mais garder les clés distinctes évite toute ambiguïté
# si ce registre de verrous devait un jour être relu globalement.
_RECONCILIATION_LOCK_KEY = 4210004

_scheduler: BackgroundScheduler | None = None


def reconciliation_job() -> None:
    """Cron nocturne (3h00) : réconcilie StatsFacturation/StatsPaiements de
    toutes les campagnes connues depuis Facturation et Paiement Service."""
    import django

    django.setup()

    from django.db import connection

    from stats.services import ReconciliateurStats

    # Verrou consultatif PostgreSQL : une seule instance réconcilie à la fois
    # (anti double-calcul en réplication). SQLite (tests) n'a pas
    # pg_try_advisory_lock → on saute simplement le verrou.
    use_lock = connection.vendor == "postgresql"
    if use_lock:  # pragma: no cover
        with connection.cursor() as cur:
            cur.execute("SELECT pg_try_advisory_lock(%s)", [_RECONCILIATION_LOCK_KEY])
            if not cur.fetchone()[0]:
                logger.info("ReconciliationJob ignoré — verrou détenu par une autre instance.")
                return

    try:
        nb_ok, nb_echecs = ReconciliateurStats().reconcilier_toutes_campagnes()
        logger.info("ReconciliationJob terminé : %s campagne(s) réconciliée(s), %s échec(s).", nb_ok, nb_echecs)
    except Exception as exc:
        logger.exception("ReconciliationJob échoué : %s", exc)
    finally:
        if use_lock:  # pragma: no cover
            with connection.cursor() as cur:
                cur.execute("SELECT pg_advisory_unlock(%s)", [_RECONCILIATION_LOCK_KEY])


def start_scheduler() -> None:
    """Démarre le scheduler APScheduler en arrière-plan."""
    global _scheduler
    if _scheduler and _scheduler.running:
        return

    _scheduler = BackgroundScheduler()
    _scheduler.add_job(
        reconciliation_job,
        trigger=CronTrigger(hour=3, minute=0),
        id="reporting_reconciliation",
        # Un conteneur redémarré peu après 3h00 ne doit pas attendre 24h de
        # plus pour rattraper la réconciliation manquée (même garde-fou que
        # `impaye_checker_job` / `campagne_planifiee_job`).
        misfire_grace_time=6 * 60 * 60,
        coalesce=True,
        replace_existing=True,
    )
    _scheduler.start()
    logger.info("ReportingScheduler démarré — réconciliation nocturne à 03:00.")


def stop_scheduler() -> None:
    """Arrête proprement le scheduler."""
    global _scheduler
    if _scheduler and _scheduler.running:
        _scheduler.shutdown(wait=False)
        logger.info("ReportingScheduler arrêté.")
