import json
import logging

logger = logging.getLogger(__name__)

CHANNEL = "config:events"


def publish_config_event(cle: str, event_type: str = "CONFIG_UPDATED") -> None:
    """Publie un événement sur Redis pour notifier la gateway (souscriptions GraphQL).

    Appelé après chaque modification d'un paramètre dans le servicer gRPC. `cle`
    permet à la gateway de filtrer la souscription `configUpdated(cle)`. L'échec
    Redis ne fait jamais échouer l'opération principale (même contrat que
    abonnes/event_publisher.py).
    """
    try:
        from django.conf import settings
        import redis

        r = redis.Redis.from_url(settings.REDIS_URL, decode_responses=True, socket_connect_timeout=1)
        payload = json.dumps({"event_type": event_type, "cle": cle})
        r.publish(CHANNEL, payload)
        r.close()  # type: ignore[no-untyped-call]  # redis-py : Redis.close() n'est pas annoté
    except Exception as exc:
        logger.warning("publish_config_event ignoré (Redis indisponible) : %s", exc)
