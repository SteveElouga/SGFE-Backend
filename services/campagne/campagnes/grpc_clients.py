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
        # Import tardif : les stubs ne sont pas encore générés pour abonne_service
        # dans ce service, on utilise grpc.unary_unary si nécessaire.
        # Pour l'instant, l'intégration sera faite via le grpc_server.py.
        self._address = address

    def ping(self) -> bool:
        """Vérifie si le service est joignable."""
        try:
            grpc.channel_ready_future(self._channel).result(timeout=2)
            return True
        except grpc.FutureTimeoutError:
            return False


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

    def notifier_campagne_cloturee(self, campagne_id: str) -> bool:
        """Déclenche la génération des factures après clôture d'une campagne.

        Retourne True si l'appel a réussi, False sinon (dégradation gracieuse).
        """
        try:
            self._stub.GenererFactures(self._pb.GenererFacturesRequest(campagne_id=campagne_id))
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
