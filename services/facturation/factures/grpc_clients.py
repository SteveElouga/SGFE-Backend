"""Clients gRPC vers les services externes consommés par Facturation Service."""

import logging
import sys
from pathlib import Path

import grpc
from django.conf import settings

from .pdf_generator import InfosSociete

logger = logging.getLogger(__name__)


class CampagneServiceClient:
    """Client gRPC vers Campagne Service (port 50053) — lecture des relevés."""

    def __init__(self) -> None:
        address = f"{settings.CAMPAGNE_GRPC_HOST}:{settings.CAMPAGNE_GRPC_PORT}"
        self._channel = grpc.insecure_channel(address)

        proto_path = str(Path(settings.BASE_DIR) / "proto")
        if proto_path not in sys.path:
            sys.path.insert(0, proto_path)

        import campagne_service_pb2 as pb
        import campagne_service_pb2_grpc as pb_grpc

        self._stub = pb_grpc.CampagneServiceStub(self._channel)
        self._pb = pb

    def list_releves(self, campagne_id: str) -> list[dict]:
        """Retourne la liste des relevés RELEVE (index saisi) pour une campagne."""
        try:
            response = self._stub.ListReleves(
                self._pb.CampagneIdRequest(campagne_id=campagne_id)
            )
            return [
                {
                    "abonne_id": r.abonne_id,
                    "ancien_index": r.ancien_index,
                    "nouveau_index": r.nouveau_index,
                    "consommation": r.consommation,
                    "date_releve": r.date_releve,
                    "statut": r.statut,
                }
                for r in response.releves
                if r.statut == "RELEVE"
            ]
        except grpc.RpcError as exc:
            logger.warning(
                "Erreur gRPC lors de la récupération des relevés",
                extra={"campagne_id": campagne_id, "error": str(exc)},
            )
            raise


class ConfigServiceClient:
    """Client gRPC vers Config Service (port 50058) — paramètres système."""

    def __init__(self) -> None:
        address = f"{settings.CONFIG_GRPC_HOST}:{settings.CONFIG_GRPC_PORT}"
        self._channel = grpc.insecure_channel(address)

        proto_path = str(Path(settings.BASE_DIR) / "proto")
        if proto_path not in sys.path:
            sys.path.insert(0, proto_path)

        import config_service_pb2 as pb
        import config_service_pb2_grpc as pb_grpc

        self._stub = pb_grpc.ConfigServiceStub(self._channel)
        self._pb = pb

    def get_delai_paiement_jours(self) -> int:
        """Retourne le délai de paiement (en jours) depuis Config Service.

        Retourne la valeur par défaut (5) si le service est inaccessible.
        """
        try:
            response = self._stub.GetConfig(
                self._pb.ConfigKeyRequest(cle="delai_paiement_jours")
            )
            return int(response.valeur)
        except Exception:
            logger.warning(
                "Config Service inaccessible — délai paiement par défaut appliqué",
                extra={"default": settings.DEFAULT_DELAI_PAIEMENT_JOURS},
            )
            return settings.DEFAULT_DELAI_PAIEMENT_JOURS

    def get_infos_societe(self) -> InfosSociete:
        """Retourne les informations de la société pour le PDF.

        Retourne des valeurs vides si le service est inaccessible.
        """
        try:
            r = self._stub.GetInfosSociete(self._pb.EmptyRequest())
            return InfosSociete(
                nom=r.nom or "SGFE", adresse=r.adresse, telephone=r.telephone
            )
        except Exception:
            logger.warning(
                "Config Service inaccessible — infos société par défaut pour PDF"
            )
            return InfosSociete()
