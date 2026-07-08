"""Implémentation du serveur gRPC du Paiement Service."""

import logging
import sys
from datetime import date
from pathlib import Path

import grpc
from django.conf import settings

# Assure que le dossier proto/ est dans sys.path avant les imports générés
sys.path.insert(0, str(Path(settings.BASE_DIR) / "proto"))

import paiement_service_pb2 as pb
import paiement_service_pb2_grpc as pb_grpc

from paiements.event_publisher import publish_paiement_event
from paiements.grpc_clients import FacturationServiceClient, ReportingServiceClient
from paiements.grpc_interceptors import ErrorHandlingInterceptor
from paiements.models import StatutSolde
from paiements.serializers import paiement_to_proto, solde_to_proto, suivi_to_proto
from paiements.services import PaiementService

logger = logging.getLogger(__name__)


class PaiementServicer(pb_grpc.PaiementServiceServicer):
    """Implémentation de tous les RPCs du PaiementService.

    Les exceptions (ValidationError, ObjectDoesNotExist, ValueError) ne sont pas
    interceptées ici : ErrorHandlingInterceptor s'en charge de façon centralisée
    (voir grpc_interceptors.py).
    """

    def __init__(self) -> None:
        self._svc = PaiementService()
        self._facturation_client = FacturationServiceClient()
        self._reporting_client = ReportingServiceClient()

    def InitialiserSolde(
        self,
        request: pb.InitialiserSoldeRequest,
        context: grpc.ServicerContext,
    ) -> pb.SoldeResponse:
        """Initialise le solde d'une facture nouvellement générée."""
        date_limite = date.fromisoformat(request.date_limite_paiement)
        solde = self._svc.initialiser_solde(
            facture_id=request.facture_id,
            abonne_id=request.abonne_id,
            montant_total=request.montant_total,
            date_limite_paiement=date_limite,
            campagne_id=request.campagne_id,
        )
        return solde_to_proto(solde)

    def EnregistrerPaiement(
        self,
        request: pb.EnregistrerPaiementRequest,
        context: grpc.ServicerContext,
    ) -> pb.PaiementResponse:
        """Enregistre un versement et met à jour le solde de la facture."""
        date_paiement = date.fromisoformat(request.date_paiement)
        paiement, solde = self._svc.enregistrer_paiement(
            facture_id=request.facture_id,
            abonne_id=request.abonne_id,
            montant=request.montant,
            date_paiement=date_paiement,
            mode_paiement=request.mode_paiement,
            reference_transaction=request.reference_transaction,
            enregistre_par=request.enregistre_par,
        )

        # Synchronisation du statut vers Facturation Service (dégradation gracieuse)
        try:
            self._facturation_client.update_statut_facture(
                facture_id=request.facture_id,
                statut=solde.statut,
            )
        except Exception as exc:
            logger.warning(
                "Sync statut facture échouée — dégradation gracieuse",
                extra={"facture_id": request.facture_id, "error": str(exc)},
            )

        # Résolution ou suspension des relances
        self._svc.marquer_facture_payee_si_applicable(solde)
        self._svc.suspendre_relances_si_partiel(solde)

        # Pousse les stats de paiement au Reporting Service (read model aval,
        # dégradation gracieuse — ADR-019). campagne_id porté par le solde.
        self._reporting_client.update_stats_paiements(
            campagne_id=solde.campagne_id,
            montant_paiement=float(request.montant),
            type_update="PAIEMENT",
        )
        if solde.statut == StatutSolde.PAYEE:
            self._reporting_client.update_stats_paiements(
                campagne_id=solde.campagne_id,
                montant_paiement=0.0,
                type_update="IMPAYE_RESOLU",
            )

        # Notifie la gateway (souscription paiementCree) — événement
        # auto-porteur, avec le statut de facture résultant.
        publish_paiement_event(paiement, statut_facture=solde.statut)

        return paiement_to_proto(paiement)

    def GetSolde(
        self,
        request: pb.FactureIdRequest,
        context: grpc.ServicerContext,
    ) -> pb.SoldeResponse:
        """Retourne le solde courant d'une facture."""
        solde = self._svc.get_solde(request.facture_id)
        return solde_to_proto(solde)

    def ListPaiements(
        self,
        request: pb.ListPaiementsRequest,
        context: grpc.ServicerContext,
    ) -> pb.ListPaiementsResponse:
        """Liste les paiements filtrés par facture et/ou abonné."""
        paiements = self._svc.list_paiements(
            facture_id=request.facture_id,
            abonne_id=request.abonne_id,
        )
        return pb.ListPaiementsResponse(paiements=[paiement_to_proto(p) for p in paiements])

    def ListPaiementsParCampagne(
        self,
        request: pb.CampagneIdRequest,
        context: grpc.ServicerContext,
    ) -> pb.ListPaiementsResponse:
        """Liste les paiements de toutes les factures d'une campagne (export CSV)."""
        paiements = self._svc.list_paiements_par_campagne(campagne_id=request.campagne_id)
        return pb.ListPaiementsResponse(paiements=[paiement_to_proto(p) for p in paiements])

    def ListImpayes(
        self,
        request: pb.EmptyRequest,
        context: grpc.ServicerContext,
    ) -> pb.ListImpayesResponse:
        """Retourne toutes les factures impayées dont la date limite est dépassée."""
        impayes = self._svc.list_impayes()
        return pb.ListImpayesResponse(impayes=[solde_to_proto(s) for s in impayes])

    def GetSuiviImpaye(
        self,
        request: pb.FactureIdRequest,
        context: grpc.ServicerContext,
    ) -> pb.SuiviImpayeResponse:
        """Retourne le suivi d'impayé pour une facture."""
        suivi = self._svc.get_suivi_impaye(request.facture_id)
        return suivi_to_proto(suivi)


def serve() -> None:
    """Démarre le serveur gRPC (appelé par la commande de management)."""
    import concurrent.futures

    server = grpc.server(
        concurrent.futures.ThreadPoolExecutor(max_workers=10),
        interceptors=[ErrorHandlingInterceptor()],
    )
    pb_grpc.add_PaiementServiceServicer_to_server(PaiementServicer(), server)
    port = getattr(settings, "PAIEMENT_GRPC_PORT", 50055)
    server.add_insecure_port(f"[::]:{port}")
    server.start()
    logger.info("Paiement gRPC server démarré sur le port %d", port)
    server.wait_for_termination()
