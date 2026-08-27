"""APScheduler cron du Paiement Service — vérification des impayés à 8h00."""

import logging

from apscheduler.schedulers.background import BackgroundScheduler
from apscheduler.triggers.cron import CronTrigger

logger = logging.getLogger(__name__)

# Verrou consultatif PostgreSQL du cron impayés : une seule instance escalade à la
# fois, même en cas de réplication (anti relances/suspensions dupliquées).
_IMPAYE_LOCK_KEY = 4210001

_scheduler: BackgroundScheduler | None = None


def impaye_checker_job() -> None:  # pragma: no cover
    """
    Cron 8h00 : vérifie toutes les factures impayées et escalade les relances.

    Délais configurables via Config Service :
    - Étape 1 (J+0)  : 1er rappel WhatsApp
    - Étape 2 (J+3)  : 2ème rappel WhatsApp
    - Étape 3 (J+7)  : Avertissement de suspension
    - Étape 4 (J+10) : Suspension de l'abonné + notification

    Dégradation gracieuse si les services externes sont indisponibles.
    """
    import django

    django.setup()

    from django.db import connection

    from paiements.services import ImpayeService

    # Verrou consultatif PostgreSQL : une seule instance escalade à la fois
    # (anti relances/suspensions dupliquées en réplication). SQLite (tests) n'a
    # pas pg_try_advisory_lock → on saute simplement le verrou.
    use_lock = connection.vendor == "postgresql"
    if use_lock:
        with connection.cursor() as cur:
            cur.execute("SELECT pg_try_advisory_lock(%s)", [_IMPAYE_LOCK_KEY])
            if not cur.fetchone()[0]:
                logger.info("ImpayeCheckerJob ignoré — verrou détenu par une autre instance.")
                return

    try:
        ImpayeService().verifier_et_escalader()
        logger.info("ImpayeCheckerJob terminé avec succès.")
    except Exception as exc:
        logger.exception("ImpayeCheckerJob échoué : %s", exc)
    finally:
        if use_lock:
            with connection.cursor() as cur:
                cur.execute("SELECT pg_advisory_unlock(%s)", [_IMPAYE_LOCK_KEY])


def start_scheduler() -> None:
    """Démarre le scheduler APScheduler en arrière-plan."""
    global _scheduler
    if _scheduler and _scheduler.running:
        return

    _scheduler = BackgroundScheduler()
    _scheduler.add_job(
        impaye_checker_job,
        trigger=CronTrigger(hour=8, minute=0),
        id="impaye_checker",
        # APScheduler abandonne un passage dont l'heure est dépassée de plus
        # d'une seconde — c'est son défaut. Sur une machine qui dort, ou un
        # conteneur redémarré après l'heure, le job n'est jamais rattrapé :
        # les journaux ne montrent que « Run time of job … was missed ».
        #
        # Constaté sur cet environnement : six factures à 31 jours de retard
        # et **zéro** SuiviImpaye en base — aucune relance n'était partie,
        # aucun abonné suspendu, alors que les quatre étapes auraient dû se
        # déclencher. Six heures de grâce laissent le temps d'un réveil ;
        # `coalesce` évite de rejouer N fois plusieurs passages manqués.
        misfire_grace_time=6 * 60 * 60,
        coalesce=True,
        replace_existing=True,
    )
    _scheduler.start()
    logger.info("PaiementScheduler démarré — cron à 08:00 tous les jours.")
