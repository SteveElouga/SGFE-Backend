import asyncio
import json
import logging
from typing import AsyncGenerator

import strawberry
from django.conf import settings
from strawberry.types import Info

from schema.abonne_types import Abonne, abonne_from_grpc
from schema.grpc_clients import abonne_client

logger = logging.getLogger(__name__)


@strawberry.type
class Subscription:
    @strawberry.subscription
    async def abonne_updated(
        self,
        info: Info,
        abonne_id: strawberry.ID | None = strawberry.UNSET,
    ) -> AsyncGenerator[Abonne, None]:
        """Pousse l'abonné mis à jour dès qu'une mutation le modifie côté backend.

        - Sans filtre  → toutes les modifications (page liste).
        - abonneId=ID  → uniquement cet abonné (page détail).

        Le frontend peut utiliser `subscribeToMore` d'Apollo Client pour
        fusionner automatiquement le résultat dans son cache local.
        """
        from redis.asyncio import Redis

        filter_id = str(abonne_id) if abonne_id and abonne_id is not strawberry.UNSET else None

        redis = Redis.from_url(settings.REDIS_URL, decode_responses=True)
        pubsub = redis.pubsub()
        await pubsub.subscribe("abonne:events")

        try:
            async for message in pubsub.listen():
                if message["type"] != "message":
                    continue

                try:
                    data = json.loads(message["data"])
                except (json.JSONDecodeError, TypeError):
                    continue

                event_abonne_id: str = data.get("abonne_id", "")
                if not event_abonne_id:
                    continue

                # Filtre optionnel : ne pousser que si ça concerne l'abonné demandé
                if filter_id and event_abonne_id != filter_id:
                    continue

                # Appel gRPC synchrone exécuté dans un thread pour ne pas
                # bloquer la boucle asyncio du serveur ASGI
                try:
                    response = await asyncio.to_thread(abonne_client.get_abonne, event_abonne_id)
                    yield abonne_from_grpc(response)
                except Exception as exc:
                    logger.warning("abonne_updated: GetAbonne(%s) échoué : %s", event_abonne_id, exc)
        finally:
            await pubsub.unsubscribe("abonne:events")
            await redis.aclose()
