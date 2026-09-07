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
        from parametres.redis_sentinel import get_redis_master

        r = get_redis_master(decode_responses=True)
        payload = json.dumps({"event_type": event_type, "cle": cle})
        r.publish(CHANNEL, payload)
        r.close()  # type: ignore[no-untyped-call]  # redis-py : Redis.close() n'est pas annoté
    except Exception as exc:
        logger.warning("publish_config_event ignoré (Redis indisponible) : %s", exc)
