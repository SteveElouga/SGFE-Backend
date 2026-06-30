import json
import logging

logger = logging.getLogger(__name__)

CHANNEL = "abonne:events"


def publish_abonne_event(abonne_id: str, event_type: str = "ABONNE_UPDATED") -> None:
    """Publie un événement sur Redis pour notifier la gateway (souscriptions GraphQL).

    Appelé après chaque mutation dans le servicer gRPC. L'échec Redis ne fait
    jamais échouer l'opération principale : les clients recevront simplement
    les données à jour à leur prochain poll.
    """
    try:
        from django.conf import settings
        import redis

        r = redis.Redis.from_url(settings.REDIS_URL, decode_responses=True, socket_connect_timeout=1)
        payload = json.dumps({"event_type": event_type, "abonne_id": abonne_id})
        r.publish(CHANNEL, payload)
        r.close()
    except Exception as exc:
        logger.warning("publish_abonne_event ignoré (Redis indisponible) : %s", exc)
