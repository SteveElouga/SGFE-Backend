import asyncio
import json
import logging
from typing import AsyncGenerator

import strawberry
from django.conf import settings
from strawberry.types import Info

from schema.abonne_types import Abonne, abonne_from_grpc
from schema.auth_types import User, user_from_grpc
from schema.campagne_queries import _verifier_acces_campagne
from schema.campagne_types import Progression
from schema.config_types import ConfigParam, config_from_grpc
from schema.context import AuthError, require_auth, require_role
from schema.facturation_types import Facture, Tarif, facture_from_grpc, tarif_from_grpc
from schema.grpc_clients import (
    abonne_client,
    auth_client,
    campagne_client,
    config_client,
    facturation_client,
    notification_client,
)
from schema.communication_types import Diffusion, diffusion_from_grpc
from schema.notification_types import WhatsAppQr, whatsapp_qr_from_grpc
from schema.paiement_types import Paiement, paiement_from_event

logger = logging.getLogger(__name__)


async def _paiement_dans_campagne(data: dict[str, object], campagne_id: str) -> bool:
    """True si le paiement appartient à la campagne, via sa facture liée."""
    try:
        facture = await asyncio.to_thread(facturation_client.get_facture, str(data.get("facture_id", "")))
    except Exception as exc:
        logger.warning("paiement_cree: filtrage campagne échoué : %s", exc)
        return False
    return bool(facture.campagne_id == campagne_id)


async def _resoudre_operateur(enregistre_par: str) -> str:
    """Résout un user_id (enregistre_par) en username affichable, best-effort."""
    if not enregistre_par:
        return ""
    try:
        return str((await asyncio.to_thread(auth_client.get_user, enregistre_par)).username)
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


async def _autoriser_acces_progression(info: Info, filter_id: str | None) -> None:
    """Garde d'accès de progressionUpdated (miroir de la query progression).

    ADMIN / AGENT / SUPERVISEUR. Sur une campagne donnée, `_verifier_acces_campagne`
    contrôle la propriété (SUPERVISEUR = créateur, AGENT = affecté ; ADMIN libre).
    Le flux global (sans campagne) est réservé à l'ADMIN — un SUPERVISEUR/AGENT
    doit préciser une campagne dont il a l'accès pour ne pas voir les autres.
    """
    user = await asyncio.to_thread(require_role, info, "ADMIN", "AGENT", "SUPERVISEUR")
    if filter_id:
        # Lève PermissionError si l'utilisateur n'a pas accès à cette campagne.
        await asyncio.to_thread(_verifier_acces_campagne, user, filter_id)
    elif user.role != "ADMIN":
        raise AuthError(
            "Précisez une campagne : le flux global est réservé à l'ADMIN",
            code="PERMISSION_DENIED",
        )


@strawberry.type
class Subscription:
    @strawberry.subscription()  # type: ignore[untyped-decorator]  # voir mypy.ini
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

    @strawberry.subscription()  # type: ignore[untyped-decorator]  # voir mypy.ini
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

        # On s'abonne AVANT de prendre le snapshot.
        #
        # L'ordre inverse ouvre une fenêtre : le snapshot traverse la gateway, le
        # service notification puis whatsapp-service en HTTP, et pendant ce
        # trajet un QR publié n'a personne pour l'entendre. L'abonné voyait alors
        # « le service démarre » jusqu'au QR suivant, une vingtaine de secondes
        # plus tard — assez long pour qu'on recharge la page en croyant à une
        # panne. S'abonner d'abord ne coûte rien et ne perd rien.
        redis = Redis.from_url(settings.REDIS_URL, decode_responses=True)
        pubsub = redis.pubsub()
        await pubsub.subscribe("whatsapp:events")

        # Snapshot initial : état courant immédiat (le QR peut déjà être prêt).
        # Appel gRPC synchrone déporté dans un thread pour ne pas bloquer l'event
        # loop ASGI ; un échec (services indisponibles) ne doit pas tuer le flux.
        try:
            snapshot = await asyncio.to_thread(notification_client.get_whatsapp_qr)
            yield whatsapp_qr_from_grpc(snapshot)
        except Exception as exc:
            logger.warning("whatsapp_status: snapshot initial échoué : %s", exc)

        try:
            async for message in pubsub.listen():
                if message["type"] != "message":
                    continue

                try:
                    data = json.loads(message["data"])
                except (json.JSONDecodeError, TypeError):
                    continue

                ready = bool(data.get("ready", False))
                yield WhatsAppQr(
                    ready=ready,
                    qr=data.get("qr", "") or "",
                    number=data.get("number", "") or "",
                    phase=data.get("phase") or ("connecte" if ready else "demarrage"),
                    depuis_ms=int(data.get("depuis") or 0),
                )
        finally:
            await pubsub.unsubscribe("whatsapp:events")
            await redis.aclose()

    @strawberry.subscription()  # type: ignore[untyped-decorator]  # voir mypy.ini
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

    @strawberry.subscription()  # type: ignore[untyped-decorator]  # voir mypy.ini
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

    @strawberry.subscription()  # type: ignore[untyped-decorator]  # voir mypy.ini
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

    @strawberry.subscription()  # type: ignore[untyped-decorator]  # voir mypy.ini
    async def config_updated(
        self,
        info: Info,
        cle: str | None = strawberry.UNSET,
    ) -> AsyncGenerator[ConfigParam, None]:
        """Pousse un paramètre système dès sa modification — ADMIN.

        - Sans filtre → tout changement de paramètre (`updateConfig`).
        - cle=String  → uniquement ce paramètre.
        """
        await asyncio.to_thread(require_role, info, "ADMIN")

        from redis.asyncio import Redis

        filter_cle = str(cle) if cle and cle is not strawberry.UNSET else None

        redis = Redis.from_url(settings.REDIS_URL, decode_responses=True)
        pubsub = redis.pubsub()
        await pubsub.subscribe("config:events")

        try:
            async for message in pubsub.listen():
                if message["type"] != "message":
                    continue

                try:
                    data = json.loads(message["data"])
                except (json.JSONDecodeError, TypeError):
                    continue

                event_cle: str = data.get("cle", "")
                if not event_cle or (filter_cle and event_cle != filter_cle):
                    continue

                try:
                    response = await asyncio.to_thread(config_client.get_config, event_cle)
                    yield config_from_grpc(response)
                except Exception as exc:
                    logger.warning("config_updated: GetConfig(%s) échoué : %s", event_cle, exc)
        finally:
            await pubsub.unsubscribe("config:events")
            await redis.aclose()

    @strawberry.subscription()  # type: ignore[untyped-decorator]  # voir mypy.ini
    async def tarif_updated(self, info: Info) -> AsyncGenerator[Tarif, None]:
        """Pousse le tarif actif dès sa modification — ADMIN/COMPTABLE.

        Un seul tarif actif à la fois : pas d'argument, la souscription re-fetch
        `GetTarifActuel` à chaque changement (`updateTarif`).
        """
        await asyncio.to_thread(require_role, info, "ADMIN", "COMPTABLE")

        from redis.asyncio import Redis

        redis = Redis.from_url(settings.REDIS_URL, decode_responses=True)
        pubsub = redis.pubsub()
        await pubsub.subscribe("tarif:events")

        try:
            async for message in pubsub.listen():
                if message["type"] != "message":
                    continue

                try:
                    response = await asyncio.to_thread(facturation_client.get_tarif_actuel)
                    yield tarif_from_grpc(response)
                except Exception as exc:
                    logger.warning("tarif_updated: GetTarifActuel échoué : %s", exc)
        finally:
            await pubsub.unsubscribe("tarif:events")
            await redis.aclose()

    @strawberry.subscription()  # type: ignore[untyped-decorator]  # voir mypy.ini
    async def progression_updated(
        self,
        info: Info,
        campagne_id: strawberry.ID | None = strawberry.UNSET,
    ) -> AsyncGenerator[Progression, None]:
        """Pousse la progression d'une campagne à chaque saisie d'index.

        - campagneId=ID → cette campagne (ADMIN, AGENT affecté, SUPERVISEUR créateur).
        - Sans filtre   → toutes les campagnes, réservé ADMIN.

        Le contrôle d'accès (voir _autoriser_acces_progression) reprend celui de
        la query `progression`.
        """
        filter_id = str(campagne_id) if campagne_id and campagne_id is not strawberry.UNSET else None
        await _autoriser_acces_progression(info, filter_id)

        from redis.asyncio import Redis

        redis = Redis.from_url(settings.REDIS_URL, decode_responses=True)
        pubsub = redis.pubsub()
        await pubsub.subscribe("progression:events")

        try:
            async for message in pubsub.listen():
                if message["type"] != "message":
                    continue

                try:
                    data = json.loads(message["data"])
                except (json.JSONDecodeError, TypeError):
                    continue

                event_campagne_id: str = data.get("campagne_id", "")
                if not event_campagne_id or (filter_id and event_campagne_id != filter_id):
                    continue

                try:
                    r = await asyncio.to_thread(campagne_client.get_progression, event_campagne_id)
                    yield Progression(
                        campagne_id=r.campagne_id,
                        total_abonnes=r.total_abonnes,
                        nb_releves=r.nb_releves,
                        nb_en_attente=r.nb_en_attente,
                        pourcentage=r.pourcentage,
                    )
                except Exception as exc:
                    logger.warning("progression_updated: GetProgression(%s) échoué : %s", event_campagne_id, exc)
        finally:
            await pubsub.unsubscribe("progression:events")
            await redis.aclose()

    @strawberry.subscription()  # type: ignore[untyped-decorator]  # voir mypy.ini
    async def diffusion_progression_updated(
        self,
        info: Info,
        diffusion_id: strawberry.ID | None = strawberry.UNSET,
    ) -> AsyncGenerator[Diffusion, None]:
        """Pousse la progression d'une diffusion à chaque lot traité par le job
        de fond du Notification Service — ADMIN uniquement (même portée que la
        query `diffusions`).

        - diffusionId=ID → cette diffusion seulement.
        - Sans filtre     → toutes les diffusions (l'écran de suivi global).
        """
        await asyncio.to_thread(require_role, info, "ADMIN")
        filter_id = str(diffusion_id) if diffusion_id and diffusion_id is not strawberry.UNSET else None

        from redis.asyncio import Redis

        redis = Redis.from_url(settings.REDIS_URL, decode_responses=True)
        pubsub = redis.pubsub()
        await pubsub.subscribe("diffusion:events")

        try:
            async for message in pubsub.listen():
                if message["type"] != "message":
                    continue

                try:
                    data = json.loads(message["data"])
                except (json.JSONDecodeError, TypeError):
                    continue

                event_diffusion_id: str = data.get("diffusion_id", "")
                if not event_diffusion_id or (filter_id and event_diffusion_id != filter_id):
                    continue

                try:
                    r = await asyncio.to_thread(notification_client.get_diffusion, event_diffusion_id)
                    cree_par = await _resoudre_operateur(r.created_by)
                    yield diffusion_from_grpc(r, cree_par=cree_par)
                except Exception as exc:
                    logger.warning(
                        "diffusion_progression_updated: GetDiffusion(%s) échoué : %s", event_diffusion_id, exc
                    )
        finally:
            await pubsub.unsubscribe("diffusion:events")
            await redis.aclose()
