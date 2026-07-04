import json
import logging

logger = logging.getLogger(__name__)

CHANNEL = "user:events"


def publish_user_event(user_id: str, event_type: str = "USER_UPDATED") -> None:
    """Publie un événement sur Redis pour notifier la gateway (souscriptions GraphQL).

    Appelé après chaque mutation utilisateur dans le servicer gRPC. Permet à la
    gateway de pousser `utilisateurUpdated` en temps réel — y compris le cas
    « profil » (un utilisateur voit immédiatement un changement de son rôle ou sa
    désactivation). L'échec Redis ne fait jamais échouer l'opération principale
    (même contrat que abonnes/event_publisher.py).
    """
    try:
        from django.conf import settings
        import redis

        r = redis.Redis.from_url(settings.REDIS_URL, decode_responses=True, socket_connect_timeout=1)
        payload = json.dumps({"event_type": event_type, "user_id": user_id})
        r.publish(CHANNEL, payload)
        r.close()
    except Exception as exc:
        logger.warning("publish_user_event ignoré (Redis indisponible) : %s", exc)
