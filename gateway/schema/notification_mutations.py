"""Mutations GraphQL du Notification Service."""

import strawberry
import strawberry.types

from .context import require_auth, require_role
from .grpc_clients import notification_client
from .notification_types import Envoi, envoi_from_grpc


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
