import asyncio
import json
import logging
from typing import AsyncGenerator

import strawberry
from django.conf import settings
from strawberry.types import Info

from schema.abonne_types import Abonne, abonne_from_grpc
from schema.context import require_role
from schema.grpc_clients import abonne_client, notification_client
from schema.notification_types import WhatsAppQr, whatsapp_qr_from_grpc

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

        Réservé à ADMIN, comme les queries `abonne`/`abonnes` équivalentes
        (voir ANO-015 dans docs/ETAT_DU_SYSTEME.md — cette subscription était
        auparavant accessible à tout client WebSocket sans authentification).
        """
        await asyncio.to_thread(require_role, info, "ADMIN")

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

    @strawberry.subscription
    async def whatsapp_status(self, info: Info) -> AsyncGenerator[WhatsAppQr, None]:
        """Pousse en temps réel le statut de connexion WhatsApp + le QR — ADMIN.

        Remplace le polling de la query `whatsappQr` : dès que whatsapp-service
        change d'état (nouveau QR, connecté, déconnecté), il publie sur le canal
        Redis `whatsapp:events` et l'événement est poussé au navigateur via
        WebSocket. Un snapshot initial est émis à l'abonnement pour afficher
        immédiatement l'état courant sans attendre le prochain événement.

        Réservé à ADMIN, comme la query `whatsappQr` équivalente.
        """
        await asyncio.to_thread(require_role, info, "ADMIN")

        from redis.asyncio import Redis

        # Snapshot initial : état courant immédiat (le QR peut déjà être prêt).
        # Appel gRPC synchrone déporté dans un thread pour ne pas bloquer l'event
        # loop ASGI ; un échec (services indisponibles) ne doit pas tuer le flux.
        try:
            snapshot = await asyncio.to_thread(notification_client.get_whatsapp_qr)
            yield whatsapp_qr_from_grpc(snapshot)
        except Exception as exc:
            logger.warning("whatsapp_status: snapshot initial échoué : %s", exc)

        redis = Redis.from_url(settings.REDIS_URL, decode_responses=True)
        pubsub = redis.pubsub()
        await pubsub.subscribe("whatsapp:events")

        try:
            async for message in pubsub.listen():
                if message["type"] != "message":
                    continue

                try:
                    data = json.loads(message["data"])
                except (json.JSONDecodeError, TypeError):
                    continue

                yield WhatsAppQr(
                    ready=bool(data.get("ready", False)),
                    qr=data.get("qr", "") or "",
                    number=data.get("number", "") or "",
                )
        finally:
            await pubsub.unsubscribe("whatsapp:events")
            await redis.aclose()
