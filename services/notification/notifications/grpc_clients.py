"""Clients gRPC vers les services externes consommés par Notification Service."""

import logging
import sys
from pathlib import Path

import grpc
from django.conf import settings

logger = logging.getLogger(__name__)

# Valeur de repli si Config Service est inaccessible
_DEFAULT_TOKEN_VALIDITE_JOURS = 20


def _get_proto_path() -> str:
    """Retourne le chemin vers les stubs gRPC générés."""
    return str(Path(settings.BASE_DIR) / "proto")


class FacturationServiceClient:
    """Client gRPC vers Facturation Service (port 50054)."""

    def __init__(self) -> None:
        address = f"{settings.FACTURATION_GRPC_HOST}:{settings.FACTURATION_GRPC_PORT}"
        self._channel = grpc.insecure_channel(address)

        proto_path = _get_proto_path()
        if proto_path not in sys.path:
            sys.path.insert(0, proto_path)

        import facturation_service_pb2 as pb  # type: ignore[import]
        import facturation_service_pb2_grpc as pb_grpc  # type: ignore[import]

        self._stub = pb_grpc.FacturationServiceStub(self._channel)
        self._pb = pb

    def get_facture(self, facture_id: str):
        """Récupère les détails d'une facture depuis Facturation Service.

        Returns:
            FactureResponse protobuf.

        Raises:
            grpc.RpcError: Si le service est inaccessible ou la facture introuvable.
        """
        return self._stub.GetFacture(self._pb.FactureIdRequest(facture_id=facture_id))

    def get_facture_pdf(self, facture_id: str) -> tuple[bytes, str]:
        """Récupère le PDF d'une facture depuis Facturation Service.

        Returns:
            Tuple (pdf_content: bytes, filename: str).
            Retourne (b"", "") en cas d'erreur — dégradation gracieuse.
        """
        try:
            response = self._stub.GetFacturePDF(self._pb.FactureIdRequest(facture_id=facture_id))
            return response.pdf_content, response.filename
        except grpc.RpcError as exc:
            logger.warning("Impossible de récupérer le PDF facture %s : %s", facture_id, exc)
            return b"", ""

    def generer_recu_paiement_pdf(self, paiement_id: str, facture_id: str) -> tuple[bytes, str]:
        """Récupère le reçu PDF d'un versement depuis Facturation Service.

        Returns:
            Tuple (pdf_content: bytes, filename: str).
            Retourne (b"", "") en cas d'erreur — dégradation gracieuse (le message
            de confirmation est tout de même envoyé sans la pièce jointe).
        """
        try:
            response = self._stub.GenererRecuPaiementPDF(
                self._pb.GenererRecuRequest(paiement_id=paiement_id, facture_id=facture_id)
            )
            return response.pdf_content, response.filename
        except grpc.RpcError as exc:
            logger.warning("Impossible de récupérer le reçu PDF (paiement %s) : %s", paiement_id, exc)
            return b"", ""


class PaiementServiceClient:
    """Client gRPC vers Paiement Service (port 50055).

    Ajouté pour que le message WhatsApp puisse annoncer le **même total** que
    le PDF qu'il transporte. Avant, le message affichait `facture.montant` — la
    consommation du mois seule — pendant que sa pièce jointe additionnait la
    dette antérieure et retranchait l'avoir. Deux chiffres différents dans le
    même envoi, et l'abonné paie celui qu'il lit dans WhatsApp.

    Les deux appels dégradent gracieusement : si Paiement est indisponible, le
    message part avec la consommation seule plutôt que de ne pas partir. Une
    facture incomplète vaut mieux qu'une facture jamais reçue — c'est la même
    règle que celle du générateur PDF.
    """

    def __init__(self) -> None:
        address = f"{settings.PAIEMENT_GRPC_HOST}:{settings.PAIEMENT_GRPC_PORT}"
        self._channel = grpc.insecure_channel(address)

        proto_path = _get_proto_path()
        if proto_path not in sys.path:
            sys.path.insert(0, proto_path)

        import paiement_service_pb2 as pb  # type: ignore[import]
        import paiement_service_pb2_grpc as pb_grpc  # type: ignore[import]

        self._stub = pb_grpc.PaiementServiceStub(self._channel)
        self._pb = pb

    def get_dette_abonne(self, abonne_id: str, hors_facture_id: str = ""):
        """Dette de l'abonné, hors la facture qu'on lui envoie.

        Retourne `(total_du, nb_factures, plus_ancienne_echeance)`. Un échec
        rend un solde nul : le message part sans la ligne d'antériorité.
        """
        try:
            r = self._stub.GetDetteAbonne(
                self._pb.DetteAbonneRequest(abonne_id=abonne_id, hors_facture_id=hors_facture_id)
            )
            return float(r.total_du), int(r.nb_factures), r.plus_ancienne_echeance or ""
        except grpc.RpcError as exc:
            logger.warning("Solde antérieur indisponible — le message part sans la ligne : %s", exc)
            return 0.0, 0, ""

    def get_avoir_impute(self, facture_id: str) -> float:
        """Part de cette facture réglée par un avoir plutôt que par un versement.

        Sans cette ligne, l'abonné lit un total inférieur à sa consommation
        sans rien qui l'explique — et croit à une erreur.
        """
        try:
            r = self._stub.GetSolde(self._pb.FactureIdRequest(facture_id=facture_id))
            return float(getattr(r, "avoir_impute", 0) or 0)
        except grpc.RpcError as exc:
            logger.warning("Avoir imputé indisponible — le message part sans la ligne : %s", exc)
            return 0.0


class AbonneServiceClient:
    """Client gRPC vers Abonné Service (port 50052)."""

    def __init__(self) -> None:
        address = f"{settings.ABONNE_GRPC_HOST}:{settings.ABONNE_GRPC_PORT}"
        self._channel = grpc.insecure_channel(address)

        proto_path = _get_proto_path()
        if proto_path not in sys.path:
            sys.path.insert(0, proto_path)

        import abonne_service_pb2 as pb  # type: ignore[import]
        import abonne_service_pb2_grpc as pb_grpc  # type: ignore[import]

        self._stub = pb_grpc.AbonneServiceStub(self._channel)
        self._pb = pb

    def get_abonne(self, abonne_id: str):
        """Récupère les informations d'un abonné depuis Abonné Service.

        Returns:
            AbonneResponse protobuf.

        Raises:
            grpc.RpcError: Si le service est inaccessible ou l'abonné introuvable.
        """
        return self._stub.GetAbonne(self._pb.AbonneIdRequest(abonne_id=abonne_id))


class ConfigServiceClient:
    """Client gRPC vers Config Service (port 50058)."""

    def __init__(self) -> None:
        address = f"{settings.CONFIG_GRPC_HOST}:{settings.CONFIG_GRPC_PORT}"
        self._channel = grpc.insecure_channel(address)

        proto_path = _get_proto_path()
        if proto_path not in sys.path:
            sys.path.insert(0, proto_path)

        import config_service_pb2 as pb  # type: ignore[import]
        import config_service_pb2_grpc as pb_grpc  # type: ignore[import]

        self._stub = pb_grpc.ConfigServiceStub(self._channel)
        self._pb = pb

    def get_infos_societe(self):
        """Récupère les informations de la société (nom, téléphone…).

        Returns:
            InfosSocieteResponse protobuf.

        Raises:
            grpc.RpcError: Si le service est inaccessible.
        """
        return self._stub.GetInfosSociete(self._pb.EmptyRequest())

    def get_token_validite_jours(self) -> int:
        """Récupère la durée de validité des tokens d'accès abonné (clé : token_validite_jours).

        En cas d'erreur (service indisponible, clé absente), retourne la valeur
        par défaut configurée dans settings (DEFAULT_TOKEN_VALIDITE_JOURS).
        """
        try:
            response = self._stub.GetConfig(self._pb.ConfigKeyRequest(cle="token_validite_jours"))
            return int(response.valeur)
        except (grpc.RpcError, ValueError) as exc:
            logger.warning(
                "Impossible de récupérer token_validite_jours depuis Config Service — "
                "utilisation de la valeur par défaut %d : %s",
                settings.DEFAULT_TOKEN_VALIDITE_JOURS,
                exc,
            )
            return settings.DEFAULT_TOKEN_VALIDITE_JOURS

    def get_email_admin_notifications(self) -> str:
        """Récupère l'email de notification admin (clé : email_admin_notifications).

        Retourne une chaîne vide si la clé est absente ou le service indisponible.
        """
        try:
            response = self._stub.GetConfig(self._pb.ConfigKeyRequest(cle="email_admin_notifications"))
            return response.valeur.strip()
        except (grpc.RpcError, ValueError):
            return ""

    def get_notifications_admin_activees(self) -> bool:
        """Vérifie si les notifications admin sont activées (clé : notifications_admin_activees).

        Retourne True par défaut si la clé est absente ou le service indisponible.
        """
        try:
            response = self._stub.GetConfig(self._pb.ConfigKeyRequest(cle="notifications_admin_activees"))
            return response.valeur.strip().lower() not in ("false", "0", "non", "no")
        except (grpc.RpcError, ValueError):
            return True


# Instances singleton utilisées dans services.py
facturation_client = FacturationServiceClient()
abonne_client = AbonneServiceClient()
config_client = ConfigServiceClient()
paiement_client = PaiementServiceClient()
