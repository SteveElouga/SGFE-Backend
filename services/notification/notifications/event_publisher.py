"""Publication d'événements Redis du Notification Service.

Même patron que `campagnes/event_publisher.py` (`publish_progression_event`) :
la gateway écoute ce canal pour sa subscription GraphQL
`diffusionProgressionUpdated`, ré-interroge l'état authoritatif via gRPC à la
réception plutôt que de faire confiance au contenu du message.
"""

import json
import logging

logger = logging.getLogger(__name__)

CHANNEL = "diffusion:events"


def publish_diffusion_event(diffusion_id: str) -> None:
    """Notifie la gateway qu'une diffusion a progressé (envoi résolu ou
    diffusion terminée). Best-effort : un Redis indisponible ne fait jamais
    échouer le traitement du lot."""
    try:
        from django.conf import settings
        import redis

        r = redis.Redis.from_url(settings.REDIS_URL, decode_responses=True, socket_connect_timeout=1)
        r.publish(CHANNEL, json.dumps({"diffusion_id": diffusion_id}))
        r.close()  # type: ignore[no-untyped-call]  # redis-py : Redis.close() n'est pas annoté
    except Exception as exc:
        logger.warning("publish_diffusion_event ignoré (Redis indisponible) : %s", exc)
