import json
import logging

logger = logging.getLogger(__name__)

CHANNEL = "facture:events"
TARIF_CHANNEL = "tarif:events"


def publish_facture_event(
    facture_id: str,
    campagne_id: str,
    event_type: str = "FACTURE_UPDATED",
) -> None:
    """Publie un événement sur Redis pour notifier la gateway (souscriptions GraphQL).

    Appelé après chaque mutation de facture dans le servicer gRPC. `campagne_id`
    est inclus pour permettre à la gateway de filtrer la souscription
    `factureUpdated(campagneId)`. L'échec Redis ne fait jamais échouer
    l'opération principale : les clients recevront les données à jour à leur
    prochaine requête (même contrat que abonnes/event_publisher.py).
    """
    try:
        from django.conf import settings
        import redis

        r = redis.Redis.from_url(settings.REDIS_URL, decode_responses=True, socket_connect_timeout=1)
        payload = json.dumps({"event_type": event_type, "facture_id": facture_id, "campagne_id": campagne_id})
        r.publish(CHANNEL, payload)
        r.close()
    except Exception as exc:
        logger.warning("publish_facture_event ignoré (Redis indisponible) : %s", exc)


def publish_tarif_event(event_type: str = "TARIF_UPDATED") -> None:
    """Publie un événement sur Redis à chaque changement du tarif actif.

    Il n'existe qu'un seul tarif actif à la fois : l'événement ne porte pas d'id,
    la gateway re-fetch le tarif courant via GetTarifActuel. Best-effort.
    """
    try:
        from django.conf import settings
        import redis

        r = redis.Redis.from_url(settings.REDIS_URL, decode_responses=True, socket_connect_timeout=1)
        r.publish(TARIF_CHANNEL, json.dumps({"event_type": event_type}))
        r.close()
    except Exception as exc:
        logger.warning("publish_tarif_event ignoré (Redis indisponible) : %s", exc)
