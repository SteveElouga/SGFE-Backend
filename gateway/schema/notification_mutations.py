"""Mutations GraphQL du Notification Service."""

import strawberry
import strawberry.types

from .context import require_auth, require_role
from .grpc_clients import notification_client
from .notification_types import Envoi, TestEnvoiResult, envoi_from_grpc


@strawberry.type
class NotificationMutations:
    @strawberry.mutation
    def envoyer_facture_whatsapp(self, info: strawberry.types.Info, facture_id: str, abonne_id: str) -> Envoi:
        """Envoie la facture par WhatsApp à l'abonné — ADMIN, COMPTABLE."""
        require_auth(info)
        require_role(info, "ADMIN", "COMPTABLE")
        return envoi_from_grpc(notification_client.envoyer_facture(facture_id=facture_id, abonne_id=abonne_id))

    @strawberry.mutation
    def renvoyer_facture_whatsapp(self, info: strawberry.types.Info, facture_id: str) -> Envoi:
        """Renvoie la facture par WhatsApp (déjà générée) — ADMIN, COMPTABLE."""
        require_auth(info)
        require_role(info, "ADMIN", "COMPTABLE")
        return envoi_from_grpc(notification_client.renvoyer_facture(facture_id=facture_id))

    @strawberry.mutation
    def revoquer_token_abonne(self, info: strawberry.types.Info, token_id: str) -> bool:
        """Révoque un token d'accès abonné — ADMIN."""
        require_auth(info)
        require_role(info, "ADMIN")
        response = notification_client.revoquer_token(token_id=token_id)
        return response.success

    @strawberry.mutation
    def revoquer_tous_tokens_abonnes(self, info: strawberry.types.Info) -> int:
        """Révoque tous les tokens d'accès abonné actifs — ADMIN.

        Retourne le nombre de tokens révoqués.
        """
        require_auth(info)
        require_role(info, "ADMIN")
        return notification_client.revoquer_tous_tokens().count

    @strawberry.mutation
    def tester_envoi_whatsapp(self, info: strawberry.types.Info, phone_number: str) -> TestEnvoiResult:
        """Envoie un message de test WhatsApp au numéro fourni — ADMIN.

        Retourne `success=false` avec le motif exact si l'envoi échoue
        (WhatsApp non connecté, numéro invalide, service injoignable).
        """
        require_auth(info)
        require_role(info, "ADMIN")
        response = notification_client.tester_envoi(phone_number=phone_number)
        return TestEnvoiResult(success=response.success, message=response.message)
