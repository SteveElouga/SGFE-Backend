"""Implémentation du serveur gRPC du Campagne Service."""

import logging
import sys
from pathlib import Path

import grpc
from django.conf import settings
from django.core.exceptions import ObjectDoesNotExist, ValidationError
from django.db import IntegrityError

# Le fichier _grpc.py généré fait un `import campagne_service_pb2` bare —
# il faut que le dossier proto/ soit dans sys.path avant l'import.
sys.path.insert(0, str(Path(settings.BASE_DIR) / "proto"))

import campagne_service_pb2 as pb
import campagne_service_pb2_grpc as pb_grpc

from campagnes.event_publisher import publish_progression_event, publish_reporting_event
from campagnes.grpc_clients import FacturationServiceClient
from campagnes.grpc_interceptors import ErrorHandlingInterceptor
from campagnes.models import StatutCampagne, StatutReleve
from campagnes.repositories import (
    CampagneAgentRepository,
    CampagneRepository,
    ReleveRepository,
)
from campagnes.serializers import agent_affecte_to_proto, campagne_to_proto, releve_to_proto
from campagnes.services import CampagneService, ReleveService

logger = logging.getLogger(__name__)


class CampagneServicer(pb_grpc.CampagneServiceServicer):
    """Implémentation de tous les RPCs du CampagneService.

    Le mapping exception -> code gRPC (ObjectDoesNotExist->NOT_FOUND,
    ValidationError->INVALID_ARGUMENT) est centralisé dans
    ErrorHandlingInterceptor (voir grpc_interceptors.py) — pas de try/except ici.
    """

    def __init__(self) -> None:
        self._campagne_svc = CampagneService()
        self._releve_svc = ReleveService()
        self._releve_repo = ReleveRepository()
        self._campagne_repo = CampagneRepository()
        self._agent_repo = CampagneAgentRepository()
        self._facturation_client = FacturationServiceClient()

    # ------------------------------------------------------------------ #
    # Campagnes
    # ------------------------------------------------------------------ #

    def CreateCampagne(
        self,
        request: pb.CreateCampagneRequest,
        context: grpc.ServicerContext,
    ) -> pb.CampagneResponse:
        """Crée une nouvelle campagne de relevé."""
        campagne = self._campagne_svc.creer_campagne(
            nom=request.nom,
            periode_mois=request.periode_mois,
            periode_annee=request.periode_annee,
            created_by=request.created_by,
            date_planifiee=request.date_planifiee or None,
            numero_mobile_money=request.numero_mobile_money,
            generer_factures_auto=request.generer_factures_auto,
            envoyer_whatsapp_auto=request.envoyer_whatsapp_auto,
            demarrer_maintenant=request.demarrer_maintenant,
        )
        return campagne_to_proto(campagne)

    def GetCampagne(
        self,
        request: pb.CampagneIdRequest,
        context: grpc.ServicerContext,
    ) -> pb.CampagneResponse:
        """Retourne les détails d'une campagne."""
        campagne = self._campagne_svc.get_campagne(request.campagne_id)
        return campagne_to_proto(campagne)

    def ListCampagnes(
        self,
        request: pb.ListCampagnesRequest,
        context: grpc.ServicerContext,
    ) -> pb.ListCampagnesResponse:
        """Liste les campagnes — filtre optionnel par créateur (SUPERVISEUR) ou agent affecté (AGENT)."""
        campagnes = self._campagne_svc.list_campagnes(
            created_by=request.created_by,
            agent_id=request.agent_id,
        )
        return pb.ListCampagnesResponse(campagnes=[campagne_to_proto(c) for c in campagnes])

    def AssignerAgent(
        self,
        request: pb.AssignerAgentRequest,
        context: grpc.ServicerContext,
    ) -> pb.CampagneResponse:
        """Affecte un agent à une campagne — idempotent."""
        campagne = self._campagne_repo.get_by_id(request.campagne_id)
        self._agent_repo.assigner(campagne=campagne, agent_id=request.agent_id)
        return campagne_to_proto(campagne)

    def AffecterZones(
        self,
        request: pb.AffecterZonesRequest,
        context: grpc.ServicerContext,
    ) -> pb.ListAgentsCampagneResponse:
        """Affecte un agent à un ensemble de zones (remplace ses zones actuelles)."""
        zones = [(z.quartier, z.camp) for z in request.zones]
        agents = self._campagne_svc.affecter_zones(request.campagne_id, request.agent_id, zones)
        return pb.ListAgentsCampagneResponse(agents=[agent_affecte_to_proto(a) for a in agents])

    def ListAgentsCampagne(
        self,
        request: pb.CampagneIdRequest,
        context: grpc.ServicerContext,
    ) -> pb.ListAgentsCampagneResponse:
        """Liste les agents affectés à une campagne (global et/ou par zone) + stats."""
        agents = self._campagne_svc.list_agents_campagne(request.campagne_id)
        return pb.ListAgentsCampagneResponse(agents=[agent_affecte_to_proto(a) for a in agents])

    def DemarrerCampagne(
        self,
        request: pb.CampagneIdRequest,
        context: grpc.ServicerContext,
    ) -> pb.CampagneResponse:
        """Démarre une campagne PLANIFIEE (→ EN_COURS) à la demande.

        Même transition que le cron 7h, mais déclenchée manuellement (bouton
        ADMIN/SUPERVISEUR) sans attendre la date planifiée. La validation
        « seule une PLANIFIEE peut être démarrée » est faite par le service
        (ValidationError → INVALID_ARGUMENT via l'ErrorHandlingInterceptor).
        """
        campagne = self._campagne_svc.demarrer_campagne(request.campagne_id)
        return campagne_to_proto(campagne)

    def CloturerCampagne(
        self,
        request: pb.CampagneIdRequest,
        context: grpc.ServicerContext,
    ) -> pb.CampagneResponse:
        """Clôture une campagne EN_COURS et notifie Facturation Service."""
        campagne = self._campagne_svc.cloturer_campagne(request.campagne_id)
        if campagne.generer_factures_auto:
            self._facturation_client.notifier_campagne_cloturee(
                str(campagne.id),
                numero_mobile_money=campagne.numero_mobile_money,
                envoyer_whatsapp_auto=campagne.envoyer_whatsapp_auto,
            )
        # Publie les stats de campagne sur le flux Reporting (CampagneCloturee,
        # read model aval, événementiel durable — ADR-019).
        stats = self._campagne_svc.get_stats_reporting(str(campagne.id))
        publish_reporting_event(
            "CAMPAGNE_STATS",
            campagne_id=str(campagne.id),
            nom_campagne=stats["nom_campagne"],
            total_abonnes=stats["total_abonnes"],
            nb_releves=stats["nb_releves"],
            consommation_totale=stats["consommation_totale"],
        )
        return campagne_to_proto(campagne)

    def GetProgression(
        self,
        request: pb.CampagneIdRequest,
        context: grpc.ServicerContext,
    ) -> pb.ProgressionResponse:
        """Retourne la progression d'une campagne (nb relevés par statut)."""
        counts = self._campagne_svc.get_progression(request.campagne_id)
        nb_releves = counts.get(StatutReleve.RELEVE, 0)
        nb_non_releve = counts.get(StatutReleve.NON_RELEVE, 0)
        nb_estime = counts.get(StatutReleve.ESTIME, 0)
        nb_a_relever = counts.get(StatutReleve.A_RELEVER, 0)
        total = nb_releves + nb_non_releve + nb_estime + nb_a_relever
        nb_traites = nb_releves + nb_non_releve + nb_estime
        pourcentage = (nb_traites / total * 100) if total > 0 else 0.0
        return pb.ProgressionResponse(
            campagne_id=request.campagne_id,
            total_abonnes=total,
            nb_releves=nb_releves,
            nb_en_attente=nb_a_relever,
            pourcentage=pourcentage,
        )

    def GetResumeCloture(
        self,
        request: pb.CampagneIdRequest,
        context: grpc.ServicerContext,
    ) -> pb.ResumeClotureResponse:
        """Aperçu de clôture prêt à afficher (modal de confirmation)."""
        r = self._campagne_svc.get_resume_cloture(request.campagne_id)
        return pb.ResumeClotureResponse(
            campagne_id=request.campagne_id,
            total_abonnes=r["total_abonnes"],
            nb_releves=r["nb_releves"],
            nb_estimes=r["nb_estimes"],
            nb_non_releves=r["nb_non_releves"],
            nb_restants=r["nb_restants"],
            nb_factures_a_generer=r["nb_factures_a_generer"],
        )

    # ------------------------------------------------------------------ #
    # Relevés
    # ------------------------------------------------------------------ #

    def AjouterAbonnesCampagne(
        self,
        request: pb.AjouterAbonnesCampagneRequest,
        context: grpc.ServicerContext,
    ) -> pb.AjouterAbonnesResponse:
        """Rattache des abonnés à une campagne en pré-créant leurs relevés A_RELEVER.

        C'est le lien manquant entre « abonnés sélectionnés » et la campagne :
        sans lui, aucun relevé n'existe avant la première saisie et « abonnés à
        relever » vaut 0. Lot robuste : la campagne est validée une fois
        (NOT_FOUND si absente, INVALID_ARGUMENT si clôturée), puis chaque abonné
        déjà inscrit ou non ACTIF est simplement ignoré (le lot ne casse pas).
        """
        campagne = self._campagne_svc.get_campagne(request.campagne_id)
        if campagne.statut == StatutCampagne.CLOTUREE:
            raise ValidationError("Impossible d'ajouter des abonnés à une campagne clôturée.")

        nb_ajoutes = 0
        nb_ignores = 0
        for abonne_id in request.abonne_ids:
            try:
                dernier_index = self._get_dernier_index_value(abonne_id)
                self._campagne_svc.ajouter_abonne_campagne(
                    campagne_id=request.campagne_id,
                    abonne_id=abonne_id,
                    ancien_index=dernier_index,
                )
                nb_ajoutes += 1
            except (ValidationError, IntegrityError):
                # Abonné déjà inscrit (y compris via une course concurrente) ou
                # non ACTIF → ignoré, le lot ne casse pas.
                nb_ignores += 1
        return pb.AjouterAbonnesResponse(nb_ajoutes=nb_ajoutes, nb_ignores=nb_ignores)

    def SaisirIndex(
        self,
        request: pb.SaisirIndexRequest,
        context: grpc.ServicerContext,
    ) -> pb.ReleveResponse:
        """
        Saisit le nouvel index d'un abonné pour une campagne.
        Crée le relevé si inexistant (avec ancien_index = dernier index connu ou 0).
        """
        releve = self._releve_repo.get_by_campagne_abonne(request.campagne_id, request.abonne_id)
        if releve is None:
            dernier_index = self._get_dernier_index_value(request.abonne_id)
            # Passe par ajouter_abonne_campagne (et non un create() direct)
            # pour bénéficier de la vérification du statut ACTIF de
            # l'abonné, obligatoire avant tout ajout en campagne.
            try:
                releve = self._campagne_svc.ajouter_abonne_campagne(
                    campagne_id=request.campagne_id,
                    abonne_id=request.abonne_id,
                    ancien_index=dernier_index,
                )
            except IntegrityError:
                # Course : un SaisirIndex concurrent (double-tap / retry réseau)
                # a créé le relevé entre le get et le create (contrainte unique
                # sur (campagne, abonne_id)). On récupère le relevé existant
                # plutôt que de laisser remonter une IntegrityError non mappée
                # (UNKNOWN côté client) — la saisie ci-dessous reste idempotente.
                releve = self._releve_repo.get_by_campagne_abonne(request.campagne_id, request.abonne_id)
                if releve is None:
                    raise
        releve = self._releve_svc.saisir_index(
            releve_id=str(releve.id),
            nouveau_index=request.nouveau_index,
            agent_id=request.agent_id,
            observation=request.observation,
            auteur_username=request.auteur_username,
            auteur_role=request.auteur_role,
        )
        # Notifie la gateway (souscription progressionUpdated) : l'avancement
        # de la campagne vient de changer. agent_id permet de rafraîchir la
        # carte de l'agent (statut/dernière activité) côté « détail campagne ».
        publish_progression_event(request.campagne_id, agent_id=request.agent_id)
        return releve_to_proto(releve)

    def CorrigerReleve(
        self,
        request: pb.CorrigerReleveRequest,
        context: grpc.ServicerContext,
    ) -> pb.ReleveResponse:
        """Corrige un index déjà relevé (ADMIN ou SUPERVISEUR propriétaire).

        Le relevé doit exister et être déjà RELEVE ; la correction est
        autorisée même sur une campagne CLOTUREE. Une entrée d'audit
        CORRECTION est ajoutée. Le contrôle d'accès (rôle, propriété) est
        assuré en amont par la Gateway.
        """
        releve = self._releve_repo.get_by_campagne_abonne(request.campagne_id, request.abonne_id)
        if releve is None:
            raise ObjectDoesNotExist(f"Relevé introuvable pour l'abonné {request.abonne_id} dans la campagne.")
        releve = self._releve_svc.corriger_releve(
            releve_id=str(releve.id),
            nouveau_index=request.nouveau_index,
            auteur_id=request.auteur_id,
            auteur_username=request.auteur_username,
            auteur_role=request.auteur_role,
            observation=request.observation,
        )
        return releve_to_proto(releve)

    def MarquerNonReleve(
        self,
        request: pb.MarquerNonReleveRequest,
        context: grpc.ServicerContext,
    ) -> pb.ReleveResponse:
        """Marque un relevé comme NON_RELEVE ou ESTIME."""
        releve = self._releve_repo.get_by_campagne_abonne(request.campagne_id, request.abonne_id)
        if releve is None:
            raise ObjectDoesNotExist(f"Relevé introuvable pour l'abonné {request.abonne_id} dans la campagne.")
        releve = self._releve_svc.marquer_non_releve(
            str(releve.id),
            statut=request.statut or "NON_RELEVE",
            observation=request.observation,
            agent_id=request.agent_id,
        )
        return releve_to_proto(releve)

    def GetReleve(
        self,
        request: pb.ReleveIdRequest,
        context: grpc.ServicerContext,
    ) -> pb.ReleveResponse:
        """Retourne les détails d'un relevé."""
        releve = self._releve_svc.get_releve(request.releve_id)
        return releve_to_proto(releve)

    def ListReleves(
        self,
        request: pb.CampagneIdRequest,
        context: grpc.ServicerContext,
    ) -> pb.ListRelevesResponse:
        """Liste tous les relevés d'une campagne."""
        releves = self._releve_svc.list_releves(request.campagne_id)
        return pb.ListRelevesResponse(releves=[releve_to_proto(r) for r in releves])

    def ListRelevesTournee(
        self,
        request: pb.ListRelevesTourneeRequest,
        context: grpc.ServicerContext,
    ) -> pb.ListRelevesResponse:
        """Tournée d'un agent : ses relevés saisis + les abonnés à relever de son
        périmètre (ses zones ; toute la campagne s'il n'a aucune zone affectée)."""
        releves = self._releve_svc.list_tournee(request.campagne_id, request.agent_id)
        return pb.ListRelevesResponse(releves=[releve_to_proto(r) for r in releves])

    def GetDernierIndex(
        self,
        request: pb.AbonneIdRequest,
        context: grpc.ServicerContext,
    ) -> pb.DernierIndexResponse:
        """Retourne le dernier index relevé pour un abonné (pour pré-remplissage)."""
        valeur = self._get_dernier_index_value(request.abonne_id)
        est_initial = valeur < 1e-9
        return pb.DernierIndexResponse(
            abonne_id=request.abonne_id,
            dernier_index=valeur,
            est_index_initial=est_initial,
        )

    # ------------------------------------------------------------------ #
    # Helpers privés
    # ------------------------------------------------------------------ #

    def _get_dernier_index_value(self, abonne_id: str) -> float:
        """Retourne le dernier nouveau_index pour un abonné, ou 0.0 si aucun."""
        from campagnes.models import Releve
        from campagnes.models import StatutReleve as SR

        dernier = (
            Releve.objects.filter(abonne_id=abonne_id, statut=SR.RELEVE)
            .order_by("-date_releve")
            .values_list("nouveau_index", flat=True)
            .first()
        )
        return float(dernier) if dernier is not None else 0.0


def serve() -> None:
    """Démarre le serveur gRPC (appelé par la commande de management)."""
    import concurrent.futures

    server = grpc.server(
        concurrent.futures.ThreadPoolExecutor(max_workers=10),
        interceptors=[ErrorHandlingInterceptor()],
    )
    pb_grpc.add_CampagneServiceServicer_to_server(CampagneServicer(), server)
    port = getattr(settings, "CAMPAGNE_GRPC_PORT", 50053)
    server.add_insecure_port(f"[::]:{port}")
    server.start()
    logger.info("Campagne gRPC server démarré sur le port %d", port)
    server.wait_for_termination()
