"""Implémentation du serveur gRPC du Facturation Service."""

import datetime
import logging
import sys
from concurrent import futures
from decimal import Decimal
from pathlib import Path

import grpc
from django.conf import settings

sys.path.insert(0, str(Path(settings.BASE_DIR) / "proto"))

import facturation_service_pb2 as pb
import facturation_service_pb2_grpc as pb_grpc

from .event_publisher import publish_facture_event, publish_tarif_event
from .grpc_clients import CampagneServiceClient, ConfigServiceClient
from .grpc_interceptors import ErrorHandlingInterceptor
from .grpc_auth import AuthServerInterceptor
from .serializers import facture_to_proto, tarif_to_proto
from .services import (
    BilanImpayesService,
    FactureService,
    RecuPaiementService,
    ReleveData,
    SyntheseCampagneService,
    TarifService,
)

logger = logging.getLogger(__name__)

_GRPC_MAX_WORKERS = 10


class FacturationServicer(pb_grpc.FacturationServiceServicer):
    """Implémentation de tous les RPCs du FacturationService.

    Le mapping exception -> code gRPC (ObjectDoesNotExist->NOT_FOUND,
    ValidationError->INVALID_ARGUMENT, PreconditionError->FAILED_PRECONDITION,
    grpc.RpcError->UNAVAILABLE, FileNotFoundError->INTERNAL) est centralisé dans
    ErrorHandlingInterceptor (voir grpc_interceptors.py) — pas de try/except ici.
    """

    def __init__(self) -> None:
        self._tarif_svc = TarifService()
        self._facture_svc = FactureService()
        self._bilan_svc = BilanImpayesService()
        self._synthese_svc = SyntheseCampagneService()
        self._recu_svc = RecuPaiementService()
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
        tarif = self._tarif_svc.get_tarif_actuel()
        return tarif_to_proto(tarif)

    def UpdateTarif(
        self,
        request: pb.UpdateTarifRequest,
        context: grpc.ServicerContext,
    ) -> pb.TarifResponse:
        """Crée un nouveau tarif actif en désactivant le précédent."""
        date_effet = datetime.date.fromisoformat(request.date_effet) if request.date_effet else datetime.date.today()
        tarif = self._tarif_svc.update_tarif(
            prix_m3=Decimal(str(request.prix_m3)),
            date_effet=date_effet,
        )
        # Notifie la gateway (souscription tarifUpdated).
        publish_tarif_event()
        return tarif_to_proto(tarif)

    # ------------------------------------------------------------------ #
    # Factures
    # ------------------------------------------------------------------ #

    def GenererFactures(
        self,
        request: pb.GenererFacturesRequest,
        context: grpc.ServicerContext,
    ) -> pb.GenererFacturesResponse:
        """Génère les factures pour tous les relevés RELEVE d'une campagne."""
        # Une RpcError ici (Campagne Service inaccessible) est mappée en
        # UNAVAILABLE par l'interceptor.
        releves_raw = self._campagne_client.list_releves(request.campagne_id)

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

        # Une PreconditionError (ex. aucun tarif actif) est mappée en
        # FAILED_PRECONDITION par l'interceptor.
        factures = self._facture_svc.generer_factures(
            campagne_id=request.campagne_id,
            releves=releves,
            delai_paiement_jours=delai,
            societe=societe,
            numero_mobile_money=request.numero_mobile_money,
            envoyer_whatsapp_auto=request.envoyer_whatsapp_auto,
        )

        # Notifie la gateway (souscription factureUpdated) : une facture par relevé.
        for f in factures:
            publish_facture_event(str(f.id), str(f.campagne_id), "FACTURE_CREATED")

        return pb.GenererFacturesResponse(factures=[facture_to_proto(f) for f in factures])

    def CreerRegularisation(
        self,
        request: pb.CreerRegularisationRequest,
        context: grpc.ServicerContext,
    ) -> pb.FactureResponse:
        """Constate à la main une dette antérieure à la mise en service."""
        limite = datetime.date.fromisoformat(request.date_limite_paiement) if request.date_limite_paiement else None
        facture = self._facture_svc.creer_regularisation(
            abonne_id=request.abonne_id,
            montant=request.montant,
            motif=request.motif,
            date_limite_paiement=limite,
        )
        return facture_to_proto(facture)

    def AnnulerFacture(
        self,
        request: pb.AnnulerFactureRequest,
        context: grpc.ServicerContext,
    ) -> pb.FactureResponse:
        """Annule une facture sans l'effacer, et rend à l'abonné ce qu'il a versé."""
        facture = self._facture_svc.annuler_facture(
            facture_id=request.facture_id,
            motif=request.motif,
            annule_par=request.annule_par,
        )
        return facture_to_proto(facture)

    def RegenererFacture(
        self,
        request: pb.RegenererFactureRequest,
        context: grpc.ServicerContext,
    ) -> pb.RegenererFactureResponse:
        """Annule une facture et en émet une corrigée depuis le relevé actuel.

        Le délai de paiement et les infos société sont lus ici, comme pour la
        génération : le service métier ne dépend pas de Config Service.
        """
        delai = self._config_client.get_delai_paiement_jours()
        societe = self._config_client.get_infos_societe()
        annulee, nouvelle = self._facture_svc.regenerer_facture(
            facture_id=request.facture_id,
            motif=request.motif,
            regenere_par=request.regenere_par,
            delai_paiement_jours=delai,
            societe=societe,
        )
        return pb.RegenererFactureResponse(
            annulee=facture_to_proto(annulee),
            nouvelle=facture_to_proto(nouvelle),
        )

    def GetFacture(
        self,
        request: pb.FactureIdRequest,
        context: grpc.ServicerContext,
    ) -> pb.FactureResponse:
        facture = self._facture_svc.get_facture(request.facture_id)
        return facture_to_proto(facture)

    def ListFactures(
        self,
        request: pb.ListFacturesRequest,
        context: grpc.ServicerContext,
    ) -> pb.ListFacturesResponse:
        factures = self._facture_svc.list_factures(
            campagne_id=request.campagne_id,
            abonne_id=request.abonne_id,
            statut=request.statut,
        )
        return pb.ListFacturesResponse(factures=[facture_to_proto(f) for f in factures])

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
        pdf_bytes, filename = self._facture_svc.get_pdf_bytes(request.facture_id)
        return pb.PDFResponse(pdf_content=pdf_bytes, filename=filename)

    def GenererBilanImpayesPDF(
        self,
        request: pb.EmptyRequest,
        context: grpc.ServicerContext,
    ) -> pb.PDFResponse:
        """Génère le PDF du bilan des impayés (agrégat back-office)."""
        pdf_bytes, filename = self._bilan_svc.generer_bilan_impayes_pdf()
        return pb.PDFResponse(pdf_content=pdf_bytes, filename=filename)

    def GenererSyntheseCampagnePDF(
        self,
        request: pb.CampagneIdRequest,
        context: grpc.ServicerContext,
    ) -> pb.PDFResponse:
        """Génère le PDF de synthèse d'une campagne (écran 13, stats 3 domaines)."""
        pdf_bytes, filename = self._synthese_svc.generer_synthese_campagne_pdf(request.campagne_id)
        return pb.PDFResponse(pdf_content=pdf_bytes, filename=filename)

    def GenererRecuPaiementPDF(
        self,
        request: pb.GenererRecuRequest,
        context: grpc.ServicerContext,
    ) -> pb.PDFResponse:
        """Génère le PDF du reçu d'un versement (document A5, ADMIN/COMPTABLE)."""
        pdf_bytes, filename = self._recu_svc.generer_recu_pdf(request.paiement_id, request.facture_id)
        return pb.PDFResponse(pdf_content=pdf_bytes, filename=filename)

    def UpdateStatutFacture(
        self,
        request: pb.UpdateStatutRequest,
        context: grpc.ServicerContext,
    ) -> pb.FactureResponse:
        facture = self._facture_svc.update_statut(request.facture_id, request.statut)
        # Notifie la gateway : couvre le passage IMPAYEE→PARTIELLE→PAYEE
        # déclenché par un paiement, ainsi que relances/suspensions.
        publish_facture_event(str(facture.id), str(facture.campagne_id), "FACTURE_UPDATED")
        return facture_to_proto(facture)


def serve() -> None:
    """Démarre le serveur gRPC du Facturation Service."""
    server = grpc.server(
        futures.ThreadPoolExecutor(max_workers=_GRPC_MAX_WORKERS),
        interceptors=[AuthServerInterceptor(settings.INTERNAL_GRPC_KEY), ErrorHandlingInterceptor()],
    )
    pb_grpc.add_FacturationServiceServicer_to_server(FacturationServicer(), server)
    port = settings.FACTURATION_GRPC_PORT
    server.add_insecure_port(f"[::]:{port}")
    server.start()
    logger.info("Facturation Service gRPC démarré sur le port %s", port)
    server.wait_for_termination()
