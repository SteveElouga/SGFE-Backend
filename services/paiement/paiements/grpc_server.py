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

from paiements.event_publisher import publish_paiement_event, publish_reporting_event
from paiements.grpc_clients import FacturationServiceClient, NotificationServiceClient
from paiements.grpc_interceptors import ErrorHandlingInterceptor
from paiements.grpc_auth import AuthServerInterceptor
from paiements.models import StatutSolde
from paiements.serializers import avoir_to_proto, paiement_to_proto, solde_to_proto, suivi_to_proto
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
        self._notification_client = NotificationServiceClient()

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

        # Envoi automatique du reçu de paiement à l'abonné (WhatsApp + PDF).
        # Le client garantit une dégradation gracieuse : un échec notification
        # n'impacte jamais l'enregistrement du paiement déjà committé.
        self._notification_client.envoyer_recu(
            paiement_id=str(paiement.id),
            facture_id=paiement.facture_id,
            abonne_id=paiement.abonne_id,
            montant=float(paiement.montant),
            solde_restant=float(solde.solde_restant),
        )

        # Publie les stats de paiement sur le flux Reporting (read model aval,
        # événementiel durable — ADR-019). campagne_id porté par le solde.
        publish_reporting_event(
            "PAIEMENT_STATS",
            campagne_id=solde.campagne_id,
            montant_paiement=float(request.montant),
            type_update="PAIEMENT",
        )
        if solde.statut == StatutSolde.PAYEE:
            publish_reporting_event(
                "PAIEMENT_STATS",
                campagne_id=solde.campagne_id,
                montant_paiement=0.0,
                type_update="IMPAYE_RESOLU",
            )

        # Notifie la gateway (souscription paiementCree) — événement
        # auto-porteur, avec le statut de facture résultant.
        publish_paiement_event(paiement, statut_facture=solde.statut)

        return paiement_to_proto(paiement)

    def AnnulerPaiement(
        self,
        request: pb.AnnulerPaiementRequest,
        context: grpc.ServicerContext,
    ) -> pb.PaiementResponse:
        """Annule un paiement enregistré par erreur et rétablit le solde de la facture."""
        paiement, solde = self._svc.annuler_paiement(
            paiement_id=request.paiement_id,
            motif=request.motif,
            annule_par=request.annule_par,
        )
        # Synchronise le statut de facture rétabli vers Facturation (dégradation gracieuse)
        try:
            self._facturation_client.update_statut_facture(
                facture_id=paiement.facture_id,
                statut=solde.statut,
            )
        except Exception as exc:
            logger.warning(
                "Sync statut facture (annulation) échouée — dégradation gracieuse",
                extra={"facture_id": paiement.facture_id, "error": str(exc)},
            )
        return paiement_to_proto(paiement)

    def CrediterAvoir(
        self,
        request: pb.CrediterAvoirRequest,
        context: grpc.ServicerContext,
    ) -> pb.AvoirResponse:
        """Émet un avoir manuel (note de rectification) sur le compte de l'abonné."""
        self._svc.crediter_avoir_manuel(
            abonne_id=request.abonne_id,
            montant=request.montant,
            motif=request.motif,
            cree_par=request.cree_par,
        )
        montant, mouvements = self._svc.get_avoir_abonne(request.abonne_id)
        return avoir_to_proto(request.abonne_id, montant, mouvements)

    def GetAvoirAbonne(
        self,
        request: pb.AbonneIdRequest,
        context: grpc.ServicerContext,
    ) -> pb.AvoirResponse:
        """Retourne le solde d'avoir + le journal des mouvements d'un abonné."""
        montant, mouvements = self._svc.get_avoir_abonne(request.abonne_id)
        return avoir_to_proto(request.abonne_id, montant, mouvements)

    def AnnulerSolde(
        self,
        request: pb.AnnulerSoldeRequest,
        context: grpc.ServicerContext,
    ) -> pb.AnnulerSoldeResponse:
        """Éteint le solde d'une facture annulée, en rendant l'argent déjà versé.

        Idempotent : réannuler renvoie le solde tel quel avec un report nul,
        plutôt que de créditer l'abonné une seconde fois.
        """
        solde, porte_en_avoir = self._svc.annuler_solde(facture_id=request.facture_id, motif=request.motif)
        return pb.AnnulerSoldeResponse(
            solde=solde_to_proto(solde),
            montant_porte_en_avoir=float(porte_en_avoir),
        )

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

    def GetDetteAbonne(
        self,
        request: pb.DetteAbonneRequest,
        context: grpc.ServicerContext,
    ) -> pb.DetteAbonneResponse:
        """Ce qu'un abonné doit encore, toutes factures confondues.

        `hors_facture_id` sert à l'impression : sur une facture, le « solde
        antérieur » est ce qu'il doit EN PLUS de celle qu'il tient en main.
        """
        soldes = [
            s
            for s in self._svc.list_non_soldes_par_abonne(request.abonne_id)
            if not request.hors_facture_id or s.facture_id != request.hors_facture_id
        ]
        total = self._svc.total_du_abonne(request.abonne_id, request.hors_facture_id)
        # La plus ancienne échéance dit l'âge de la dette — c'est elle qui fait
        # payer, pas le montant.
        plus_ancienne = min((s.date_limite_paiement for s in soldes), default=None)
        return pb.DetteAbonneResponse(
            total_du=float(total),
            nb_factures=len(soldes),
            plus_ancienne_echeance=plus_ancienne.isoformat() if plus_ancienne else "",
        )

    def EnregistrerPaiementAbonne(
        self,
        request: pb.EnregistrerPaiementAbonneRequest,
        context: grpc.ServicerContext,
    ) -> pb.PaiementAbonneResponse:
        """Encaisse un versement imputé du plus ancien au plus récent."""
        paiements, excedent = self._svc.enregistrer_paiement_abonne(
            abonne_id=request.abonne_id,
            montant=request.montant,
            date_paiement=date.fromisoformat(request.date_paiement),
            mode_paiement=request.mode_paiement,
            reference_transaction=request.reference_transaction,
            enregistre_par=request.enregistre_par,
        )
        return pb.PaiementAbonneResponse(
            paiements=[paiement_to_proto(p) for p in paiements],
            excedent_en_avoir=float(excedent),
        )


def serve() -> None:
    """Démarre le serveur gRPC (appelé par la commande de management)."""
    import concurrent.futures

    server = grpc.server(
        concurrent.futures.ThreadPoolExecutor(max_workers=10),
        interceptors=[AuthServerInterceptor(settings.INTERNAL_GRPC_KEY), ErrorHandlingInterceptor()],
    )
    pb_grpc.add_PaiementServiceServicer_to_server(PaiementServicer(), server)
    port = getattr(settings, "PAIEMENT_GRPC_PORT", 50055)
    server.add_insecure_port(f"[::]:{port}")
    server.start()
    logger.info("Paiement gRPC server démarré sur le port %d", port)
    server.wait_for_termination()
