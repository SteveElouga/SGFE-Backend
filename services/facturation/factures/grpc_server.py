"""Implémentation du serveur gRPC du Facturation Service."""

import datetime
import logging
import sys
from concurrent import futures
from decimal import Decimal
from pathlib import Path

import grpc
from django.conf import settings
from django.core.exceptions import ObjectDoesNotExist, ValidationError

sys.path.insert(0, str(Path(settings.BASE_DIR) / "proto"))

import facturation_service_pb2 as pb
import facturation_service_pb2_grpc as pb_grpc

from .grpc_clients import CampagneServiceClient, ConfigServiceClient
from .serializers import facture_to_proto, tarif_to_proto
from .services import FactureService, ReleveData, TarifService

logger = logging.getLogger(__name__)

_GRPC_MAX_WORKERS = 10


class FacturationServicer(pb_grpc.FacturationServiceServicer):
    """Implémentation de tous les RPCs du FacturationService."""

    def __init__(self) -> None:
        self._tarif_svc = TarifService()
        self._facture_svc = FactureService()
        self._campagne_client = CampagneServiceClient()
        self._config_client = ConfigServiceClient()

    # ------------------------------------------------------------------ #
    # Tarif
    # ------------------------------------------------------------------ #

    def GetTarifActuel(
        self,
        request: pb.EmptyRequest,
        context: grpc.ServicerContext,
    ) -> pb.TarifResponse:
        """Retourne le tarif actif (prix du m³)."""
        try:
            tarif = self._tarif_svc.get_tarif_actuel()
            return tarif_to_proto(tarif)
        except ObjectDoesNotExist:
            context.abort(grpc.StatusCode.NOT_FOUND, "Aucun tarif actif configuré.")
            return pb.TarifResponse()

    def UpdateTarif(
        self,
        request: pb.UpdateTarifRequest,
        context: grpc.ServicerContext,
    ) -> pb.TarifResponse:
        """Crée un nouveau tarif actif en désactivant le précédent."""
        try:
            date_effet = (
                datetime.date.fromisoformat(request.date_effet) if request.date_effet else datetime.date.today()
            )
            tarif = self._tarif_svc.update_tarif(
                prix_m3=Decimal(str(request.prix_m3)),
                date_effet=date_effet,
            )
            return tarif_to_proto(tarif)
        except ValidationError as exc:
            context.abort(grpc.StatusCode.INVALID_ARGUMENT, str(exc))
            return pb.TarifResponse()
        except Exception as exc:
            logger.exception("Erreur inattendue dans UpdateTarif")
            context.abort(grpc.StatusCode.INTERNAL, f"Erreur interne : {exc}")
            return pb.TarifResponse()

    # ------------------------------------------------------------------ #
    # Factures
    # ------------------------------------------------------------------ #

    def GenererFactures(
        self,
        request: pb.GenererFacturesRequest,
        context: grpc.ServicerContext,
    ) -> pb.GenererFacturesResponse:
        """Génère les factures pour tous les relevés RELEVE d'une campagne."""
        try:
            releves_raw = self._campagne_client.list_releves(request.campagne_id)
        except grpc.RpcError as exc:
            context.abort(
                grpc.StatusCode.UNAVAILABLE,
                f"Impossible de récupérer les relevés depuis Campagne Service : {exc}",
            )
            return pb.GenererFacturesResponse()

        releves = [
            ReleveData(
                abonne_id=r["abonne_id"],
                ancien_index=r["ancien_index"],
                nouveau_index=r["nouveau_index"],
                consommation=r["consommation"],
                date_releve=r["date_releve"],
            )
            for r in releves_raw
        ]

        delai = self._config_client.get_delai_paiement_jours()
        societe = self._config_client.get_infos_societe()

        try:
            factures = self._facture_svc.generer_factures(
                campagne_id=request.campagne_id,
                releves=releves,
                delai_paiement_jours=delai,
                societe=societe,
                numero_mobile_money=request.numero_mobile_money,
            )
        except ValidationError as exc:
            context.abort(grpc.StatusCode.FAILED_PRECONDITION, str(exc))
            return pb.GenererFacturesResponse()
        except Exception as exc:
            logger.exception("Erreur inattendue dans GenererFactures")
            context.abort(grpc.StatusCode.INTERNAL, f"Erreur interne : {exc}")
            return pb.GenererFacturesResponse()

        return pb.GenererFacturesResponse(factures=[facture_to_proto(f) for f in factures])

    def GetFacture(
        self,
        request: pb.FactureIdRequest,
        context: grpc.ServicerContext,
    ) -> pb.FactureResponse:
        try:
            facture = self._facture_svc.get_facture(request.facture_id)
            return facture_to_proto(facture)
        except ObjectDoesNotExist:
            context.abort(
                grpc.StatusCode.NOT_FOUND,
                f"Facture introuvable : {request.facture_id}",
            )
            return pb.FactureResponse()

    def ListFactures(
        self,
        request: pb.ListFacturesRequest,
        context: grpc.ServicerContext,
    ) -> pb.ListFacturesResponse:
        try:
            factures = self._facture_svc.list_factures(
                campagne_id=request.campagne_id,
                abonne_id=request.abonne_id,
                statut=request.statut,
            )
            return pb.ListFacturesResponse(factures=[facture_to_proto(f) for f in factures])
        except ValidationError as exc:
            context.abort(grpc.StatusCode.INVALID_ARGUMENT, str(exc))
            return pb.ListFacturesResponse()

    def GetFacturesParCampagne(
        self,
        request: pb.CampagneIdRequest,
        context: grpc.ServicerContext,
    ) -> pb.ListFacturesResponse:
        factures = self._facture_svc.list_factures(campagne_id=request.campagne_id)
        return pb.ListFacturesResponse(factures=[facture_to_proto(f) for f in factures])

    def GetFacturePDF(
        self,
        request: pb.FactureIdRequest,
        context: grpc.ServicerContext,
    ) -> pb.PDFResponse:
        try:
            pdf_bytes, filename = self._facture_svc.get_pdf_bytes(request.facture_id)
            return pb.PDFResponse(pdf_content=pdf_bytes, filename=filename)
        except ObjectDoesNotExist:
            context.abort(
                grpc.StatusCode.NOT_FOUND,
                f"Facture introuvable : {request.facture_id}",
            )
            return pb.PDFResponse()
        except FileNotFoundError as exc:
            context.abort(grpc.StatusCode.INTERNAL, str(exc))
            return pb.PDFResponse()

    def UpdateStatutFacture(
        self,
        request: pb.UpdateStatutRequest,
        context: grpc.ServicerContext,
    ) -> pb.FactureResponse:
        try:
            facture = self._facture_svc.update_statut(request.facture_id, request.statut)
            return facture_to_proto(facture)
        except ObjectDoesNotExist:
            context.abort(
                grpc.StatusCode.NOT_FOUND,
                f"Facture introuvable : {request.facture_id}",
            )
            return pb.FactureResponse()
        except ValidationError as exc:
            context.abort(grpc.StatusCode.INVALID_ARGUMENT, str(exc))
            return pb.FactureResponse()


def serve() -> None:
    """Démarre le serveur gRPC du Facturation Service."""
    server = grpc.server(futures.ThreadPoolExecutor(max_workers=_GRPC_MAX_WORKERS))
    pb_grpc.add_FacturationServiceServicer_to_server(FacturationServicer(), server)
    port = settings.FACTURATION_GRPC_PORT
    server.add_insecure_port(f"[::]:{port}")
    server.start()
    logger.info("Facturation Service gRPC démarré sur le port %s", port)
    server.wait_for_termination()
