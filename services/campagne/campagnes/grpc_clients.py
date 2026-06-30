"""Clients gRPC vers les services externes consommés par Campagne Service."""

import logging

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


class FacturationServiceClient:
    """Client gRPC vers Facturation Service (port 50054) — appel CampagneCloturee."""

    def __init__(self) -> None:
        address = f"{settings.FACTURATION_GRPC_HOST}:{settings.FACTURATION_GRPC_PORT}"
        self._channel = grpc.insecure_channel(address)
        self._address = address

    def notifier_campagne_cloturee(self, campagne_id: str) -> bool:
        """
        Notifie Facturation Service qu'une campagne a été clôturée.
        Retourne True si l'appel a réussi, False sinon (dégradation gracieuse).
        """
        try:
            # Import du stub facturation_service_pb2 quand disponible
            # Pour l'instant log uniquement — implémentation complète lors de
            # la construction du Facturation Service.
            logger.info(
                "CampagneCloturee notification vers Facturation Service",
                extra={"campagne_id": campagne_id, "address": self._address},
            )
            return True
        except Exception as exc:  # noqa: BLE001
            logger.warning(
                "Impossible de notifier Facturation Service — dégradation gracieuse",
                extra={"campagne_id": campagne_id, "error": str(exc)},
            )
            return False
