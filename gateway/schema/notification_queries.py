"""Queries GraphQL du Notification Service."""

import strawberry
import strawberry.types

from .context import require_auth, require_role
from .grpc_clients import notification_client
from .notification_types import Envoi, envoi_from_grpc


@strawberry.type
class NotificationQueries:
    @strawberry.field
    def envoi(self, info: strawberry.types.Info, envoi_id: str) -> Envoi:
        """Détails d'un envoi WhatsApp — ADMIN, COMPTABLE."""
        require_auth(info)
        require_role(info, "ADMIN", "COMPTABLE")
        return envoi_from_grpc(notification_client.get_envoi(envoi_id))

    @strawberry.field
    def envois(
        self,
        info: strawberry.types.Info,
        facture_id: str = "",
        abonne_id: str = "",
    ) -> list[Envoi]:
        """Liste des envois WhatsApp avec filtres optionnels — ADMIN, COMPTABLE."""
        require_auth(info)
        require_role(info, "ADMIN", "COMPTABLE")
        response = notification_client.list_envois(facture_id=facture_id, abonne_id=abonne_id)
        return [envoi_from_grpc(e) for e in response.envois]
