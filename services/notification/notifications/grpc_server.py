"""Serveur gRPC du Notification Service.

Implémente les RPCs définis dans notification_service.proto :
  - EnvoyerFacture
  - ReenvoyerFacture
  - EnvoyerRelance
  - GetEnvoi
  - ListEnvois
  - ValiderToken
  - RevoquerToken
"""

import logging
import sys
from concurrent import futures
from pathlib import Path

import grpc
from django.conf import settings

logger = logging.getLogger(__name__)


def _setup_proto_path() -> None:
    """Ajoute le dossier des stubs gRPC au sys.path si nécessaire."""
    proto_path = str(Path(settings.BASE_DIR) / "proto")
    if proto_path not in sys.path:
        sys.path.insert(0, proto_path)


_setup_proto_path()

import notification_service_pb2 as pb  # type: ignore[import]  # noqa: E402
import notification_service_pb2_grpc as pb_grpc  # type: ignore[import]  # noqa: E402

from notifications.grpc_interceptors import ErrorHandlingInterceptor  # noqa: E402
from notifications.serializers import envoi_to_proto, token_to_valider_response  # noqa: E402
from notifications.services import EnvoiService, TokenService, notifier_admins  # noqa: E402


class NotificationServiceServicer(pb_grpc.NotificationServiceServicer):
    """Implémentation du servicer gRPC Notification Service.

    Les exceptions (ObjectDoesNotExist, ValueError, ValidationError,
    WhatsAppDeliveryError) sont gérées de façon centralisée par
    ErrorHandlingInterceptor, sauf pour EnvoyerFacture / EnvoyerRelance
    qui utilisent une dégradation gracieuse (statut ECHEC, pas d'abort).
    """

    def __init__(self) -> None:
        self._envoi_service = EnvoiService()
        self._token_service = TokenService()

    def EnvoyerFacture(self, request, context):
        """Envoie la facture par WhatsApp.

        En cas d'échec WhatsApp, retourne un EnvoiResponse ECHEC
        sans lever d'erreur gRPC (dégradation gracieuse).
        """
        envoi = self._envoi_service.envoyer_facture(
            facture_id=request.facture_id,
            abonne_id=request.abonne_id,
        )
        return envoi_to_proto(envoi)

    def ReenvoyerFacture(self, request, context):
        """Révoque l'ancien token et renvoie la facture par WhatsApp."""
        envoi = self._envoi_service.renvoyer_facture(facture_id=request.facture_id)
        return envoi_to_proto(envoi)

    def EnvoyerRelance(self, request, context):
        """Envoie un message de relance (étapes 1 à 4).

        Lève INVALID_ARGUMENT si l'étape est hors de la plage [1, 4].
        En cas d'échec WhatsApp, retourne un EnvoiResponse ECHEC.
        """
        envoi = self._envoi_service.envoyer_relance(
            facture_id=request.facture_id,
            abonne_id=request.abonne_id,
            etape=request.etape,
        )
        return envoi_to_proto(envoi)

    def GetEnvoi(self, request, context):
        """Récupère un envoi par son UUID. Lève NOT_FOUND si absent."""
        envoi = self._envoi_service.get_envoi(envoi_id=request.envoi_id)
        return envoi_to_proto(envoi)

    def ListEnvois(self, request, context):
        """Liste les envois filtrés par facture_id et/ou abonne_id."""
        envois = self._envoi_service.list_envois(
            facture_id=request.facture_id,
            abonne_id=request.abonne_id,
        )
        return pb.ListEnvoisResponse(envois=[envoi_to_proto(e) for e in envois])

    def ValiderToken(self, request, context):
        """Valide un token d'accès abonné.

        Retourne ValiderTokenResponse(is_valid=True) si le token est actif
        et non expiré, sinon ValiderTokenResponse(is_valid=False).
        Ne lève jamais d'erreur gRPC — les tokens invalides retournent is_valid=False.
        """
        try:
            token = self._token_service.valider_token(token_str=request.token)
            return token_to_valider_response(token)
        except (ValueError, Exception):
            return pb.ValiderTokenResponse(is_valid=False, abonne_id="", date_expiration="")

    def RevoquerToken(self, request, context):
        """Révoque un token d'accès. Lève NOT_FOUND si le token est introuvable."""
        self._token_service.revoquer_token(token_id=request.token_id)
        return pb.StatusResponse(success=True, message="Token révoqué avec succès")

    def NotifierAdmins(self, request, context):
        """Envoie une notification email aux administrateurs via Brevo.

        Ne lève jamais d'erreur gRPC — dégradation gracieuse si Brevo est indisponible.
        """
        notifier_admins(
            evenement=request.evenement,
            detail=request.detail,
            entite_id=request.entite_id,
        )
        return pb.StatusResponse(success=True, message="Notification admin traitée")


def serve() -> None:
    """Démarre le serveur gRPC du Notification Service."""
    server = grpc.server(
        futures.ThreadPoolExecutor(max_workers=10),
        interceptors=[ErrorHandlingInterceptor()],
    )
    pb_grpc.add_NotificationServiceServicer_to_server(NotificationServiceServicer(), server)
    server.add_insecure_port(f"[::]:{settings.NOTIFICATION_GRPC_PORT}")
    server.start()
    logger.info(
        "Notification gRPC server démarré sur le port %d",
        settings.NOTIFICATION_GRPC_PORT,
    )
    print(f"Notification gRPC server démarré sur le port {settings.NOTIFICATION_GRPC_PORT}")
    server.wait_for_termination()
