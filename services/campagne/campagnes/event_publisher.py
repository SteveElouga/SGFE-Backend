import json
import logging
import uuid

logger = logging.getLogger(__name__)

CHANNEL = "progression:events"

# Flux Redis Streams consommé par le Reporting Service (contrat partagé).
REPORTING_STREAM = "reporting:stream"


def publish_reporting_event(event_type: str, **payload) -> None:
    """Publie un événement de stats sur le flux Redis du Reporting Service (XADD).

    Transport durable (Streams) : l'événement persiste et sera consommé même si
    Reporting est momentanément indisponible. `event_id` (UUID) permet au
    consumer de dédupliquer (idempotence). Best-effort : un échec Redis ne fait
    jamais échouer l'opération métier (Redis est supposé disponible en prod)."""
    try:
        from django.conf import settings
        import redis

        r = redis.Redis.from_url(settings.REDIS_URL, socket_connect_timeout=1)
        event = {"event_id": str(uuid.uuid4()), "type": event_type, **payload}
        r.xadd(REPORTING_STREAM, {"data": json.dumps(event)})
        r.close()
    except Exception as exc:
        logger.warning("publish_reporting_event ignoré (Redis indisponible) : %s", exc)


def publish_progression_event(campagne_id: str, event_type: str = "PROGRESSION_UPDATED", agent_id: str = "") -> None:
    """Publie un événement sur Redis pour notifier la gateway (souscriptions GraphQL).

    Appelé après chaque saisie d'index modifiant l'avancement d'une campagne.
    `campagne_id` permet à la gateway de filtrer `progressionUpdated(campagneId)`
    et d'appliquer le contrôle d'accès (SUPERVISEUR = ses propres campagnes).
    `agent_id` (facultatif) indique quel agent vient de saisir, afin que la
    gateway puisse rafraîchir la carte de cet agent (écran « détail campagne »).
    L'échec Redis ne fait jamais échouer l'opération principale.
    """
    try:
        from django.conf import settings
        import redis

        r = redis.Redis.from_url(settings.REDIS_URL, decode_responses=True, socket_connect_timeout=1)
        payload = json.dumps({"event_type": event_type, "campagne_id": campagne_id, "agent_id": agent_id})
        r.publish(CHANNEL, payload)
        r.close()
    except Exception as exc:
        logger.warning("publish_progression_event ignoré (Redis indisponible) : %s", exc)
