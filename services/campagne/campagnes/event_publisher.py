import json
import logging

logger = logging.getLogger(__name__)

CHANNEL = "progression:events"


def publish_progression_event(campagne_id: str, event_type: str = "PROGRESSION_UPDATED") -> None:
    """Publie un événement sur Redis pour notifier la gateway (souscriptions GraphQL).

    Appelé après chaque saisie d'index modifiant l'avancement d'une campagne.
    `campagne_id` permet à la gateway de filtrer `progressionUpdated(campagneId)`
    et d'appliquer le contrôle d'accès (SUPERVISEUR = ses propres campagnes).
    L'échec Redis ne fait jamais échouer l'opération principale.
    """
    try:
        from django.conf import settings
        import redis

        r = redis.Redis.from_url(settings.REDIS_URL, decode_responses=True, socket_connect_timeout=1)
        payload = json.dumps({"event_type": event_type, "campagne_id": campagne_id})
        r.publish(CHANNEL, payload)
        r.close()
    except Exception as exc:
        logger.warning("publish_progression_event ignoré (Redis indisponible) : %s", exc)
