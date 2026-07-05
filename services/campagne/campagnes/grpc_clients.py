"""Clients gRPC vers les services externes consommés par Campagne Service."""

import logging
import sys
from pathlib import Path

import grpc
from django.conf import settings

logger = logging.getLogger(__name__)


class AbonneServiceClient:
    """Client gRPC vers Abonné Service (port 50052)."""

    def __init__(self) -> None:
        address = f"{settings.ABONNE_GRPC_HOST}:{settings.ABONNE_GRPC_PORT}"
        self._channel = grpc.insecure_channel(address)
        self._address = address

        proto_path = str(Path(settings.BASE_DIR) / "proto")
        if proto_path not in sys.path:
            sys.path.insert(0, proto_path)

        import abonne_service_pb2 as pb
        import abonne_service_pb2_grpc as pb_grpc

        self._stub = pb_grpc.AbonneServiceStub(self._channel)
        self._pb = pb

    def ping(self) -> bool:
        """Vérifie si le service est joignable."""
        try:
            grpc.channel_ready_future(self._channel).result(timeout=2)
            return True
        except grpc.FutureTimeoutError:
            return False

    def get_abonne(self, abonne_id: str):
        """Récupère les informations d'un abonné depuis Abonné Service.

        Returns:
            AbonneResponse protobuf (contient notamment `statut`).

        Raises:
            grpc.RpcError: Si le service est inaccessible ou l'abonné introuvable.
        """
        return self._stub.GetAbonne(self._pb.AbonneIdRequest(abonne_id=abonne_id))


class NotificationServiceClient:
    """Client gRPC vers Notification Service (port 50056) — notifications admin."""

    def __init__(self) -> None:
        address = f"{settings.NOTIFICATION_GRPC_HOST}:{settings.NOTIFICATION_GRPC_PORT}"
        self._channel = grpc.insecure_channel(address)

        proto_path = str(Path(settings.BASE_DIR) / "proto")
        if proto_path not in sys.path:
            sys.path.insert(0, proto_path)

        import notification_service_pb2 as pb
        import notification_service_pb2_grpc as pb_grpc

        self._stub = pb_grpc.NotificationServiceStub(self._channel)
        self._pb = pb

    def notifier_admins(self, evenement: str, detail: str, entite_id: str = "") -> None:
        """Notifie les administrateurs d'un événement campagne.

        Dégradation gracieuse en cas d'erreur gRPC.
        """
        try:
            self._stub.NotifierAdmins(
                self._pb.NotifierAdminsRequest(
                    evenement=evenement,
                    detail=detail,
                    entite_id=entite_id,
                )
            )
        except Exception as exc:
            logger.warning(
                "NotifierAdmins échoué — dégradation gracieuse",
                extra={"evenement": evenement, "error": str(exc)},
            )


class FacturationServiceClient:
    """Client gRPC vers Facturation Service (port 50054) — déclenchement GenererFactures."""

    def __init__(self) -> None:
        address = f"{settings.FACTURATION_GRPC_HOST}:{settings.FACTURATION_GRPC_PORT}"
        self._channel = grpc.insecure_channel(address)

        proto_path = str(Path(settings.BASE_DIR) / "proto")
        if proto_path not in sys.path:
            sys.path.insert(0, proto_path)

        import facturation_service_pb2 as pb
        import facturation_service_pb2_grpc as pb_grpc

        self._stub = pb_grpc.FacturationServiceStub(self._channel)
        self._pb = pb

    def notifier_campagne_cloturee(
        self,
        campagne_id: str,
        numero_mobile_money: str = "",
        envoyer_whatsapp_auto: bool = True,
    ) -> bool:
        """Déclenche la génération des factures après clôture d'une campagne.

        Retourne True si l'appel a réussi, False sinon (dégradation gracieuse).
        """
        try:
            self._stub.GenererFactures(
                self._pb.GenererFacturesRequest(
                    campagne_id=campagne_id,
                    numero_mobile_money=numero_mobile_money,
                    envoyer_whatsapp_auto=envoyer_whatsapp_auto,
                )
            )
            logger.info(
                "Factures générées par Facturation Service",
                extra={"campagne_id": campagne_id},
            )
            return True
        except grpc.RpcError as exc:
            logger.warning(
                "Impossible de générer les factures — dégradation gracieuse",
                extra={"campagne_id": campagne_id, "error": str(exc)},
            )
            return False


class ReportingServiceClient:
    """Client gRPC vers Reporting Service (port 50057) — stats de campagne (ADR-019).

    Read model aval : son indisponibilité ne doit jamais faire échouer la clôture
    d'une campagne. Dégradation gracieuse (log + retour False).
    """

    def __init__(self) -> None:
        address = f"{settings.REPORTING_GRPC_HOST}:{settings.REPORTING_GRPC_PORT}"
        self._channel = grpc.insecure_channel(address)

        proto_path = str(Path(settings.BASE_DIR) / "proto")
        if proto_path not in sys.path:
            sys.path.insert(0, proto_path)

        import reporting_service_pb2 as pb
        import reporting_service_pb2_grpc as pb_grpc

        self._stub = pb_grpc.ReportingServiceStub(self._channel)
        self._pb = pb

    def update_stats_campagne(
        self,
        campagne_id: str,
        nom_campagne: str,
        total_abonnes: int,
        nb_releves: int,
        consommation_totale: float,
    ) -> bool:
        try:
            self._stub.UpdateStatsCampagne(
                self._pb.UpdateStatsCampagneRequest(
                    campagne_id=campagne_id,
                    nom_campagne=nom_campagne,
                    total_abonnes=total_abonnes,
                    nb_releves=nb_releves,
                    consommation_totale=consommation_totale,
                )
            )
            return True
        except Exception as exc:
            logger.warning(
                "Reporting Service inaccessible — UpdateStatsCampagne ignoré",
                extra={"campagne_id": campagne_id, "error": str(exc)},
            )
            return False
