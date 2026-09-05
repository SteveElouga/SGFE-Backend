"""APScheduler cron de l'Auth Service — purge RGPD automatique des comptes
utilisateurs internes désactivés depuis plus de 3 ans.

Durée de rétention validée EXPLICITEMENT par le porteur du projet (voir
`DUREE_RETENTION_UTILISATEUR_DESACTIVE`, comptes/services.py) : 3 ans après
`date_desactivation`. Même patron que les autres crons quotidiens du dépôt
(CronTrigger + verrou consultatif PostgreSQL, bypass SQLite en tests — voir
`services/paiement/paiements/schedulers.py::impaye_checker_job`).
"""

import logging

from apscheduler.schedulers.background import BackgroundScheduler
from apscheduler.triggers.cron import CronTrigger

logger = logging.getLogger(__name__)

# Verrou consultatif PostgreSQL de la purge RGPD : une seule instance purge à
# la fois, même en cas de réplication (anti double-anonymisation concurrente
# du même utilisateur). Distinct de _IMPAYE_LOCK_KEY (paiement, 4210001),
# _CAMPAGNE_LOCK_KEY / _DIFFUSION_LOCK_KEY (campagne/notification, 4210002),
# _FACTURATION_RETRY_LOCK_KEY (campagne, 4210003), _RECONCILIATION_LOCK_KEY
# (reporting, 4210004), _RETRY_ENVOIS_LOCK_KEY / _OUTBOX_RELAY_LOCK_KEY
# (notification/facturation, 4210005) — sans effet pratique ici (chaque
# service a sa propre instance PostgreSQL, voir docker-compose.yml), mais
# garder les clés distinctes évite toute ambiguïté si ce registre de verrous
# devait un jour être relu globalement (voir CLAUDE.md racine).
_PURGE_RGPD_LOCK_KEY = 4210006

_scheduler: BackgroundScheduler | None = None


def purge_rgpd_job() -> None:
    """Cron quotidien (4h00) : anonymise tout utilisateur interne désactivé
    depuis plus de 3 ans (RGPD, durée validée par le porteur du projet).

    Verrou consultatif PostgreSQL : une seule instance purge à la fois
    (anti double-anonymisation en réplication). SQLite (tests) n'a pas
    `pg_try_advisory_lock` → on saute simplement le verrou.

    Best-effort par utilisateur — voir
    `UserAdminService.purger_utilisateurs_desactives`, qui journalise déjà
    chaque échec individuel : ce job ne fait qu'orchestrer l'appel et
    journaliser le résultat global.
    """
    import django

    django.setup()

    from django.db import connection

    from comptes.services import UserAdminService

    use_lock = connection.vendor == "postgresql"
    if use_lock:  # pragma: no cover
        with connection.cursor() as cur:
            cur.execute("SELECT pg_try_advisory_lock(%s)", [_PURGE_RGPD_LOCK_KEY])
            if not cur.fetchone()[0]:
                logger.info("PurgeRgpdJob ignoré — verrou détenu par une autre instance.")
                return

    try:
        nb_ok, nb_echecs = UserAdminService().purger_utilisateurs_desactives()
        logger.info(
            "PurgeRgpdJob terminé : %s utilisateur(s) anonymisé(s), %s échec(s).",
            nb_ok,
            nb_echecs,
        )
    except Exception as exc:
        logger.exception("PurgeRgpdJob échoué : %s", exc)
    finally:
        if use_lock:  # pragma: no cover
            with connection.cursor() as cur:
                cur.execute("SELECT pg_advisory_unlock(%s)", [_PURGE_RGPD_LOCK_KEY])


def start_scheduler() -> None:
    """Démarre le scheduler APScheduler en arrière-plan."""
    global _scheduler
    if _scheduler and _scheduler.running:
        return

    _scheduler = BackgroundScheduler()
    _scheduler.add_job(
        purge_rgpd_job,
        trigger=CronTrigger(hour=4, minute=0),
        id="purge_rgpd",
        # Un conteneur redémarré peu après 4h00 ne doit pas attendre 24h de
        # plus pour rattraper la purge manquée (même garde-fou que
        # `impaye_checker_job` / `campagne_planifiee_job` / `reconciliation_job`).
        misfire_grace_time=6 * 60 * 60,
        coalesce=True,
        replace_existing=True,
    )
    _scheduler.start()
    logger.info("AuthScheduler démarré — purge RGPD quotidienne à 04:00.")


def stop_scheduler() -> None:
    """Arrête proprement le scheduler."""
    global _scheduler
    if _scheduler and _scheduler.running:
        _scheduler.shutdown(wait=False)
        logger.info("AuthScheduler arrêté.")
