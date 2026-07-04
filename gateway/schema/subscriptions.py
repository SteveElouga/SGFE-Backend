import asyncio
import json
import logging
from typing import AsyncGenerator

import strawberry
from django.conf import settings
from strawberry.types import Info

from schema.abonne_types import Abonne, abonne_from_grpc
from schema.auth_types import User, user_from_grpc
from schema.context import AuthError, require_auth, require_role
from schema.facturation_types import Facture, facture_from_grpc
from schema.grpc_clients import (
    abonne_client,
    auth_client,
    facturation_client,
    notification_client,
)
from schema.notification_types import WhatsAppQr, whatsapp_qr_from_grpc
from schema.paiement_types import Paiement, paiement_from_event

logger = logging.getLogger(__name__)


async def _paiement_dans_campagne(data: dict, campagne_id: str) -> bool:
    """True si le paiement appartient à la campagne, via sa facture liée."""
    try:
        facture = await asyncio.to_thread(facturation_client.get_facture, data.get("facture_id", ""))
    except Exception as exc:
        logger.warning("paiement_cree: filtrage campagne échoué : %s", exc)
        return False
    return facture.campagne_id == campagne_id


async def _resoudre_operateur(enregistre_par: str) -> str:
    """Résout un user_id (enregistre_par) en username affichable, best-effort."""
    if not enregistre_par:
        return ""
    try:
        return (await asyncio.to_thread(auth_client.get_user, enregistre_par)).username
    except Exception as exc:
        logger.warning("paiement_cree: résolution opérateur échouée : %s", exc)
        return ""


async def _autoriser_acces_utilisateur(info: Info, filter_id: str | None) -> None:
    """Garde d'accès de utilisateurUpdated.

    Un ADMIN peut suivre tout le monde (flux global ou filtré). Un utilisateur
    non-ADMIN ne peut suivre **que son propre compte** (cas « profil » : réagir à
    un changement de son rôle / sa désactivation) — il doit donc fournir un
    filtre égal à son propre id. Lève AuthError sinon.
    """
    payload = await asyncio.to_thread(require_auth, info)
    if payload.role == "ADMIN":
        return
    if filter_id and filter_id == payload.user_id:
        return
    raise AuthError("Accès non autorisé", code="PERMISSION_DENIED")


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

    @strawberry.subscription
    async def facture_updated(
        self,
        info: Info,
        campagne_id: strawberry.ID | None = strawberry.UNSET,
    ) -> AsyncGenerator[Facture, None]:
        """Pousse la facture mise à jour dès qu'une mutation la modifie.

        - Sans filtre    → toutes les factures (vue globale comptable).
        - campagneId=ID  → uniquement les factures de cette campagne.

        Couvre la génération (FACTURE_CREATED) et tout changement de statut
        (FACTURE_UPDATED : IMPAYEE→PARTIELLE→PAYEE via paiement, relances,
        suspensions). Réservé à ADMIN/COMPTABLE, comme les queries factures.
        """
        await asyncio.to_thread(require_role, info, "ADMIN", "COMPTABLE")

        from redis.asyncio import Redis

        filter_id = str(campagne_id) if campagne_id and campagne_id is not strawberry.UNSET else None

        redis = Redis.from_url(settings.REDIS_URL, decode_responses=True)
        pubsub = redis.pubsub()
        await pubsub.subscribe("facture:events")

        try:
            async for message in pubsub.listen():
                if message["type"] != "message":
                    continue

                try:
                    data = json.loads(message["data"])
                except (json.JSONDecodeError, TypeError):
                    continue

                if filter_id and data.get("campagne_id") != filter_id:
                    continue

                facture_id: str = data.get("facture_id", "")
                if not facture_id:
                    continue

                try:
                    response = await asyncio.to_thread(facturation_client.get_facture, facture_id)
                    yield facture_from_grpc(response)
                except Exception as exc:
                    logger.warning("facture_updated: GetFacture(%s) échoué : %s", facture_id, exc)
        finally:
            await pubsub.unsubscribe("facture:events")
            await redis.aclose()

    @strawberry.subscription
    async def paiement_cree(
        self,
        info: Info,
        campagne_id: strawberry.ID | None = strawberry.UNSET,
    ) -> AsyncGenerator[Paiement, None]:
        """Pousse chaque nouveau paiement dès son enregistrement.

        - Sans filtre    → tous les paiements.
        - campagneId=ID  → uniquement les paiements des factures de cette campagne.

        L'événement est auto-porteur (le service paiement n'expose pas de
        GetPaiement) ; le filtre campagne est résolu ici via la facture liée, et
        seulement si un filtre est demandé. Réservé à ADMIN/COMPTABLE.
        """
        await asyncio.to_thread(require_role, info, "ADMIN", "COMPTABLE")

        from redis.asyncio import Redis

        filter_id = str(campagne_id) if campagne_id and campagne_id is not strawberry.UNSET else None

        redis = Redis.from_url(settings.REDIS_URL, decode_responses=True)
        pubsub = redis.pubsub()
        await pubsub.subscribe("paiement:events")

        try:
            async for message in pubsub.listen():
                if message["type"] != "message":
                    continue

                try:
                    data = json.loads(message["data"])
                except (json.JSONDecodeError, TypeError):
                    continue

                # Le paiement porte facture_id : on remonte à la campagne via la
                # facture liée (fetch uniquement si un filtre campagne est demandé).
                if filter_id and not await _paiement_dans_campagne(data, filter_id):
                    continue

                operateur = await _resoudre_operateur(data.get("enregistre_par", ""))
                yield paiement_from_event(data, operateur=operateur)
        finally:
            await pubsub.unsubscribe("paiement:events")
            await redis.aclose()

    @strawberry.subscription
    async def utilisateur_updated(
        self,
        info: Info,
        utilisateur_id: strawberry.ID | None = strawberry.UNSET,
    ) -> AsyncGenerator[User, None]:
        """Pousse l'utilisateur mis à jour dès qu'une mutation le modifie.

        - Sans filtre (ADMIN) → toutes les créations/modifications/(dés)activations
          d'utilisateurs (vue liste admin — voir une action d'un autre admin en direct).
        - utilisateurId=ID    → uniquement cet utilisateur. Cas « profil » : un
          utilisateur non-ADMIN peut suivre son propre compte pour réagir
          immédiatement à un changement de rôle ou une désactivation (sécurité).

        Accès : ADMIN, ou l'utilisateur lui-même sur son propre id
        (voir _autoriser_acces_utilisateur).
        """
        filter_id = str(utilisateur_id) if utilisateur_id and utilisateur_id is not strawberry.UNSET else None
        await _autoriser_acces_utilisateur(info, filter_id)

        from redis.asyncio import Redis

        redis = Redis.from_url(settings.REDIS_URL, decode_responses=True)
        pubsub = redis.pubsub()
        await pubsub.subscribe("user:events")

        try:
            async for message in pubsub.listen():
                if message["type"] != "message":
                    continue

                try:
                    data = json.loads(message["data"])
                except (json.JSONDecodeError, TypeError):
                    continue

                user_id: str = data.get("user_id", "")
                if not user_id or (filter_id and user_id != filter_id):
                    continue

                try:
                    response = await asyncio.to_thread(auth_client.get_user, user_id)
                    yield user_from_grpc(response)
                except Exception as exc:
                    logger.warning("utilisateur_updated: GetUser(%s) échoué : %s", user_id, exc)
        finally:
            await pubsub.unsubscribe("user:events")
            await redis.aclose()
