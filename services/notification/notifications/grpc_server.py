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
from notifications.grpc_auth import AuthServerInterceptor  # noqa: E402
from notifications.serializers import diffusion_to_proto, envoi_to_proto, token_to_valider_response  # noqa: E402
from notifications.services import DiffusionService, EnvoiService, TokenService, notifier_admins  # noqa: E402
from notifications.whatsapp_client import WhatsAppDeliveryError  # noqa: E402


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
        self._diffusion_service = DiffusionService()

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
        """Envoie un message de relance ou de rétablissement (étapes 0 à 4).

        Lève INVALID_ARGUMENT si l'étape est hors de la plage [0, 4].
        En cas d'échec WhatsApp, retourne un EnvoiResponse ECHEC.
        """
        envoi = self._envoi_service.envoyer_relance(
            facture_id=request.facture_id,
            abonne_id=request.abonne_id,
            etape=request.etape,
            jours_avant_suspension=request.jours_avant_suspension,
        )
        return envoi_to_proto(envoi)

    def EnvoyerRecu(self, request, context):
        """Envoie le reçu de paiement (PDF) à l'abonné après un versement.

        En cas d'échec WhatsApp, retourne un EnvoiResponse ECHEC sans lever
        d'erreur gRPC (dégradation gracieuse).
        """
        envoi = self._envoi_service.envoyer_recu(
            paiement_id=request.paiement_id,
            facture_id=request.facture_id,
            abonne_id=request.abonne_id,
            montant=request.montant,
            solde_restant=request.solde_restant,
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

    def GetEspaceUrl(self, request, context):
        """Retourne l'URL de l'espace abonné (get-or-create token) pour affichage sur le PDF."""
        token = self._token_service.get_or_create_token(
            abonne_id=request.abonne_id,
            facture_id=request.facture_id,
        )
        url = f"{settings.FRONTEND_URL}/espace/{token.token}"
        return pb.EspaceUrlResponse(url=url, date_expiration=token.date_expiration.isoformat())

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

    def GetWhatsAppQr(self, request, context):
        """Retourne le statut de connexion WhatsApp, le QR de liaison et le numéro appairé.

        Destiné à l'affichage admin (via la Gateway). Ne lève jamais d'erreur
        gRPC — dégradation gracieuse si whatsapp-service est indisponible
        (ready=False, qr="", number="").
        """
        ready, qr, number, phase, depuis_ms = self._envoi_service.get_whatsapp_qr()
        return pb.WhatsAppQrResponse(ready=ready, qr=qr, number=number, phase=phase, depuis_ms=depuis_ms)

    def RevoquerTousTokens(self, request, context):
        """Révoque en masse tous les tokens d'accès abonné actifs."""
        count = self._token_service.revoquer_tous_tokens()
        return pb.RevoquerTousTokensResponse(count=count)

    def TesterEnvoi(self, request, context):
        """Envoie un message de test WhatsApp.

        Renvoie success=False + le motif réel en cas d'échec d'envoi (WhatsApp
        non connecté, numéro invalide, service injoignable) plutôt que d'abort :
        l'admin a besoin de la raison exacte, pas d'un message générique.
        Un numéro vide lève ValueError -> INVALID_ARGUMENT via l'intercepteur.
        """
        try:
            self._envoi_service.tester_envoi(request.phone_number)
        except WhatsAppDeliveryError as exc:
            return pb.StatusResponse(success=False, message=str(exc))
        return pb.StatusResponse(success=True, message="Message de test envoyé")

    def CreerDiffusion(self, request, context):
        """Crée une diffusion et une ligne d'envoi par abonné dont le
        téléphone a pu être résolu — les envois eux-mêmes partent en fond
        (`schedulers.diffusion_processor_job`), jamais depuis ce RPC."""
        diffusion = self._diffusion_service.creer_diffusion(
            message=request.message,
            abonne_ids=list(request.abonne_ids),
            created_by=request.created_by,
        )
        return diffusion_to_proto(diffusion, self._diffusion_service.compter(diffusion))

    def GetDiffusion(self, request, context):
        """Récupère une diffusion par son UUID. NOT_FOUND si absente (via
        l'intercepteur, ObjectDoesNotExist → NOT_FOUND)."""
        diffusion = self._diffusion_service.get_diffusion(request.diffusion_id)
        return diffusion_to_proto(diffusion, self._diffusion_service.compter(diffusion))

    def ListDiffusions(self, request, context):
        """Liste toutes les diffusions, la plus récente d'abord."""
        diffusions = self._diffusion_service.list_diffusions()
        return pb.ListDiffusionsResponse(
            diffusions=[diffusion_to_proto(d, self._diffusion_service.compter(d)) for d in diffusions]
        )


def serve() -> None:
    """Démarre le serveur gRPC du Notification Service."""
    server = grpc.server(
        futures.ThreadPoolExecutor(max_workers=10),
        interceptors=[AuthServerInterceptor(settings.INTERNAL_GRPC_KEY), ErrorHandlingInterceptor()],
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
