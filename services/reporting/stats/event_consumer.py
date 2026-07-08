"""Consumer Redis Streams : alimente le read model reporting à partir des
événements publiés par les autres services (campagne/facturation/paiement).

Transport : Redis Streams (XADD côté producteurs, XREADGROUP/XACK ici) —
livraison **at-least-once** via un consumer group qui persiste les entrées non
acquittées (rattrapage au redémarrage). L'idempotence est assurée par
`ProcessedEvent` (dédup `event_id`), obligatoire pour les stats à incrément.
"""

import json
import logging
import threading
import time

from django.conf import settings
from django.db import transaction

from stats.models import ProcessedEvent
from stats.services import AgregateurDashboard

logger = logging.getLogger(__name__)

STREAM_KEY = "reporting:stream"
GROUP = "reporting-consumers"
CONSUMER_NAME = "reporting"

# Types d'événements (contrat partagé avec les producteurs).
EVENT_CAMPAGNE = "CAMPAGNE_STATS"
EVENT_FACTURATION = "FACTURATION_STATS"
EVENT_PAIEMENT = "PAIEMENT_STATS"


def apply_event(agg: AgregateurDashboard, event: dict) -> None:
    """Applique un événement au read model, de façon idempotente.

    L'insertion de `ProcessedEvent` et la mise à jour des stats sont dans la même
    transaction : si l'application échoue, tout est annulé et l'événement sera
    redélivré ; s'il a déjà été traité, on ne fait rien (le XACK reste correct).
    """
    event_id = event.get("event_id")
    event_type = event.get("type")
    if not event_id:
        logger.warning("Événement sans event_id ignoré : %s", event)
        return

    with transaction.atomic():
        _, created = ProcessedEvent.objects.get_or_create(event_id=event_id, defaults={"event_type": event_type or ""})
        if not created:
            return  # déjà appliqué (rejeu at-least-once)

        if event_type == EVENT_CAMPAGNE:
            agg.update_stats_campagne(
                campagne_id=event["campagne_id"],
                nom_campagne=event.get("nom_campagne", ""),
                total_abonnes=event["total_abonnes"],
                nb_releves=event["nb_releves"],
                consommation_totale=event["consommation_totale"],
            )
        elif event_type == EVENT_FACTURATION:
            agg.update_stats_facturation(
                campagne_id=event["campagne_id"],
                delta_factures=event["delta_factures"],
                delta_montant=event["delta_montant"],
                type_update=event["type_update"],
            )
        elif event_type == EVENT_PAIEMENT:
            agg.update_stats_paiements(
                campagne_id=event["campagne_id"],
                montant_paiement=event["montant_paiement"],
                type_update=event["type_update"],
            )
        else:
            logger.warning("Type d'événement inconnu ignoré : %s", event_type)


def _connect():
    import redis

    return redis.Redis.from_url(settings.REDIS_URL, decode_responses=True, socket_connect_timeout=2)


def _ensure_group(r) -> None:
    """Crée le stream + le consumer group s'ils n'existent pas (idempotent)."""
    try:
        r.xgroup_create(STREAM_KEY, GROUP, id="0", mkstream=True)
    except Exception as exc:  # BUSYGROUP = groupe déjà présent
        if "BUSYGROUP" not in str(exc):
            raise


def _handle_entries(r, agg: AgregateurDashboard, entries) -> None:
    for _stream, msgs in entries:
        for msg_id, fields in msgs:
            try:
                event = json.loads(fields["data"])
                apply_event(agg, event)
                r.xack(STREAM_KEY, GROUP, msg_id)
            except Exception:
                # Pas de XACK : l'entrée reste "pending" et sera redélivrée.
                logger.exception("Traitement de l'événement %s échoué (sera redélivré)", msg_id)


def consume_forever(stop_event: "threading.Event | None" = None) -> None:
    """Boucle de consommation. `stop_event` permet un arrêt propre (tests)."""
    r = _connect()
    _ensure_group(r)
    agg = AgregateurDashboard()

    # Rattrapage : entrées déjà délivrées mais non acquittées (crash précédent).
    _handle_entries(r, agg, r.xreadgroup(GROUP, CONSUMER_NAME, {STREAM_KEY: "0"}, count=200))

    while stop_event is None or not stop_event.is_set():
        try:
            entries = r.xreadgroup(GROUP, CONSUMER_NAME, {STREAM_KEY: ">"}, count=50, block=2000)
            if entries:
                _handle_entries(r, agg, entries)
        except Exception:
            logger.exception("Erreur de lecture du flux reporting — nouvelle tentative dans 2s")
            time.sleep(2)
            try:
                r = _connect()
                _ensure_group(r)
            except Exception:
                logger.exception("Reconnexion Redis échouée")


def start_consumer_thread() -> "threading.Thread | None":
    """Démarre le consumer dans un thread daemon.

    Best-effort : une erreur d'initialisation (Redis indisponible) ne doit jamais
    empêcher le serveur gRPC de démarrer — le read model reste lisible, il ne
    sera simplement pas alimenté tant que Redis n'est pas joignable.
    """
    try:
        thread = threading.Thread(target=consume_forever, name="reporting-event-consumer", daemon=True)
        thread.start()
        logger.info("Consumer d'événements reporting démarré (flux %s)", STREAM_KEY)
        return thread
    except Exception:
        logger.exception("Impossible de démarrer le consumer d'événements reporting")
        return None
