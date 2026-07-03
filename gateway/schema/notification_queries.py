"""Queries GraphQL du Notification Service."""

import strawberry
import strawberry.types

from .context import require_auth, require_role
from .grpc_clients import notification_client
from .notification_types import Envoi, WhatsAppQr, envoi_from_grpc, whatsapp_qr_from_grpc


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

    @strawberry.field
    def whatsapp_qr(self, info: strawberry.types.Info) -> WhatsAppQr:
        """Statut de connexion WhatsApp + QR code de liaison — ADMIN uniquement.

        Permet à l'UI admin d'afficher le QR à scanner (« Appareils connectés »)
        sans exposer la clé interne du whatsapp-service au navigateur : la
        Gateway relaie via notification-service en gRPC. Le QR tournant côté
        WhatsApp, l'UI doit rafraîchir cette query périodiquement tant que
        `ready` est faux.
        """
        require_auth(info)
        require_role(info, "ADMIN")
        return whatsapp_qr_from_grpc(notification_client.get_whatsapp_qr())
