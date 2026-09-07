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
        from abonnes.redis_sentinel import get_redis_master

        r = get_redis_master(decode_responses=True)
        payload = json.dumps({"event_type": event_type, "abonne_id": abonne_id})
        r.publish(CHANNEL, payload)
        r.close()  # type: ignore[no-untyped-call]  # redis-py : Redis.close() n'est pas annoté
    except Exception as exc:
        logger.warning("publish_abonne_event ignoré (Redis indisponible) : %s", exc)
