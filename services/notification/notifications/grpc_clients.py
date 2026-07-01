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
            response = self._stub.GetConfig(
                self._pb.ConfigKeyRequest(cle="token_validite_jours")
            )
            return int(response.valeur)
        except (grpc.RpcError, ValueError) as exc:
            logger.warning(
                "Impossible de récupérer token_validite_jours depuis Config Service — "
                "utilisation de la valeur par défaut %d : %s",
                settings.DEFAULT_TOKEN_VALIDITE_JOURS,
                exc,
            )
            return settings.DEFAULT_TOKEN_VALIDITE_JOURS


# Instances singleton utilisées dans services.py
facturation_client = FacturationServiceClient()
abonne_client = AbonneServiceClient()
config_client = ConfigServiceClient()
