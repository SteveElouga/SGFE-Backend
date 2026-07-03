"""Implémentation du serveur gRPC du Campagne Service."""

import logging
import sys
from pathlib import Path

import grpc
from django.conf import settings
from django.core.exceptions import ObjectDoesNotExist, ValidationError

# Le fichier _grpc.py généré fait un `import campagne_service_pb2` bare —
# il faut que le dossier proto/ soit dans sys.path avant l'import.
sys.path.insert(0, str(Path(settings.BASE_DIR) / "proto"))

import campagne_service_pb2 as pb
import campagne_service_pb2_grpc as pb_grpc

from campagnes.grpc_clients import FacturationServiceClient
from campagnes.models import StatutReleve
from campagnes.repositories import (
    CampagneAgentRepository,
    CampagneRepository,
    ReleveRepository,
)
from campagnes.serializers import campagne_to_proto, releve_to_proto
from campagnes.services import CampagneService, ReleveService

logger = logging.getLogger(__name__)


class CampagneServicer(pb_grpc.CampagneServiceServicer):
    """Implémentation de tous les RPCs du CampagneService."""

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
        try:
            campagne = self._campagne_svc.creer_campagne(
                nom=request.nom,
                periode_mois=request.periode_mois,
                periode_annee=request.periode_annee,
                created_by=request.created_by,
                date_planifiee=request.date_planifiee or None,
                numero_mobile_money=request.numero_mobile_money,
                generer_factures_auto=request.generer_factures_auto,
                envoyer_whatsapp_auto=request.envoyer_whatsapp_auto,
            )
            return campagne_to_proto(campagne)
        except ValidationError as exc:
            context.abort(grpc.StatusCode.INVALID_ARGUMENT, str(exc))
        except Exception as exc:
            logger.exception("CreateCampagne échoué")
            context.abort(grpc.StatusCode.INTERNAL, str(exc))

    def GetCampagne(
        self,
        request: pb.CampagneIdRequest,
        context: grpc.ServicerContext,
    ) -> pb.CampagneResponse:
        """Retourne les détails d'une campagne."""
        try:
            campagne = self._campagne_svc.get_campagne(request.campagne_id)
            return campagne_to_proto(campagne)
        except ObjectDoesNotExist as exc:
            context.abort(grpc.StatusCode.NOT_FOUND, str(exc))
        except Exception as exc:
            logger.exception("GetCampagne échoué")
            context.abort(grpc.StatusCode.INTERNAL, str(exc))

    def ListCampagnes(
        self,
        request: pb.ListCampagnesRequest,
        context: grpc.ServicerContext,
    ) -> pb.ListCampagnesResponse:
        """Liste les campagnes — filtre optionnel par créateur (SUPERVISEUR) ou agent affecté (AGENT)."""
        try:
            campagnes = self._campagne_svc.list_campagnes(
                created_by=request.created_by,
                agent_id=request.agent_id,
            )
            return pb.ListCampagnesResponse(campagnes=[campagne_to_proto(c) for c in campagnes])
        except Exception as exc:
            logger.exception("ListCampagnes échoué")
            context.abort(grpc.StatusCode.INTERNAL, str(exc))

    def AssignerAgent(
        self,
        request: pb.AssignerAgentRequest,
        context: grpc.ServicerContext,
    ) -> pb.CampagneResponse:
        """Affecte un agent à une campagne — idempotent."""
        try:
            campagne = self._campagne_repo.get_by_id(request.campagne_id)
            self._agent_repo.assigner(campagne=campagne, agent_id=request.agent_id)
            return campagne_to_proto(campagne)
        except ObjectDoesNotExist as exc:
            context.abort(grpc.StatusCode.NOT_FOUND, str(exc))
        except Exception as exc:
            logger.exception("AssignerAgent échoué")
            context.abort(grpc.StatusCode.INTERNAL, str(exc))

    def CloturerCampagne(
        self,
        request: pb.CampagneIdRequest,
        context: grpc.ServicerContext,
    ) -> pb.CampagneResponse:
        """Clôture une campagne EN_COURS et notifie Facturation Service."""
        try:
            campagne = self._campagne_svc.cloturer_campagne(request.campagne_id)
            if campagne.generer_factures_auto:
                self._facturation_client.notifier_campagne_cloturee(
                    str(campagne.id),
                    numero_mobile_money=campagne.numero_mobile_money,
                    envoyer_whatsapp_auto=campagne.envoyer_whatsapp_auto,
                )
            return campagne_to_proto(campagne)
        except ObjectDoesNotExist as exc:
            context.abort(grpc.StatusCode.NOT_FOUND, str(exc))
        except ValidationError as exc:
            context.abort(grpc.StatusCode.INVALID_ARGUMENT, str(exc))
        except Exception as exc:
            logger.exception("CloturerCampagne échoué")
            context.abort(grpc.StatusCode.INTERNAL, str(exc))

    def GetProgression(
        self,
        request: pb.CampagneIdRequest,
        context: grpc.ServicerContext,
    ) -> pb.ProgressionResponse:
        """Retourne la progression d'une campagne (nb relevés par statut)."""
        try:
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
        except ObjectDoesNotExist as exc:
            context.abort(grpc.StatusCode.NOT_FOUND, str(exc))
        except Exception as exc:
            logger.exception("GetProgression échoué")
            context.abort(grpc.StatusCode.INTERNAL, str(exc))

    # ------------------------------------------------------------------ #
    # Relevés
    # ------------------------------------------------------------------ #

    def SaisirIndex(
        self,
        request: pb.SaisirIndexRequest,
        context: grpc.ServicerContext,
    ) -> pb.ReleveResponse:
        """
        Saisit le nouvel index d'un abonné pour une campagne.
        Crée le relevé si inexistant (avec ancien_index = dernier index connu ou 0).
        """
        try:
            releve = self._releve_repo.get_by_campagne_abonne(request.campagne_id, request.abonne_id)
            if releve is None:
                dernier_index = self._get_dernier_index_value(request.abonne_id)
                # Passe par ajouter_abonne_campagne (et non un create() direct)
                # pour bénéficier de la vérification du statut ACTIF de
                # l'abonné, obligatoire avant tout ajout en campagne.
                releve = self._campagne_svc.ajouter_abonne_campagne(
                    campagne_id=request.campagne_id,
                    abonne_id=request.abonne_id,
                    ancien_index=dernier_index,
                )
            releve = self._releve_svc.saisir_index(
                releve_id=str(releve.id),
                nouveau_index=request.nouveau_index,
                agent_id=request.agent_id,
                observation=request.observation,
            )
            return releve_to_proto(releve)
        except ObjectDoesNotExist as exc:
            context.abort(grpc.StatusCode.NOT_FOUND, str(exc))
        except ValidationError as exc:
            context.abort(grpc.StatusCode.INVALID_ARGUMENT, str(exc))
        except Exception as exc:
            logger.exception("SaisirIndex échoué")
            context.abort(grpc.StatusCode.INTERNAL, str(exc))

    def MarquerNonReleve(
        self,
        request: pb.MarquerNonReleveRequest,
        context: grpc.ServicerContext,
    ) -> pb.ReleveResponse:
        """Marque un relevé comme NON_RELEVE ou ESTIME."""
        releve = self._releve_repo.get_by_campagne_abonne(request.campagne_id, request.abonne_id)
        if releve is None:
            context.abort(
                grpc.StatusCode.NOT_FOUND,
                f"Relevé introuvable pour l'abonné {request.abonne_id} dans la campagne.",
            )
            return
        try:
            releve = self._releve_svc.marquer_non_releve(
                str(releve.id),
                statut=request.statut or "NON_RELEVE",
                observation=request.observation,
            )
            return releve_to_proto(releve)
        except ObjectDoesNotExist as exc:
            context.abort(grpc.StatusCode.NOT_FOUND, str(exc))
        except ValidationError as exc:
            context.abort(grpc.StatusCode.INVALID_ARGUMENT, str(exc))
        except Exception as exc:
            logger.exception("MarquerNonReleve échoué")
            context.abort(grpc.StatusCode.INTERNAL, str(exc))

    def GetReleve(
        self,
        request: pb.ReleveIdRequest,
        context: grpc.ServicerContext,
    ) -> pb.ReleveResponse:
        """Retourne les détails d'un relevé."""
        try:
            releve = self._releve_svc.get_releve(request.releve_id)
            return releve_to_proto(releve)
        except ObjectDoesNotExist as exc:
            context.abort(grpc.StatusCode.NOT_FOUND, str(exc))
        except Exception as exc:
            logger.exception("GetReleve échoué")
            context.abort(grpc.StatusCode.INTERNAL, str(exc))

    def ListReleves(
        self,
        request: pb.CampagneIdRequest,
        context: grpc.ServicerContext,
    ) -> pb.ListRelevesResponse:
        """Liste tous les relevés d'une campagne."""
        try:
            releves = self._releve_svc.list_releves(request.campagne_id)
            return pb.ListRelevesResponse(releves=[releve_to_proto(r) for r in releves])
        except ObjectDoesNotExist as exc:
            context.abort(grpc.StatusCode.NOT_FOUND, str(exc))
        except Exception as exc:
            logger.exception("ListReleves échoué")
            context.abort(grpc.StatusCode.INTERNAL, str(exc))

    def GetDernierIndex(
        self,
        request: pb.AbonneIdRequest,
        context: grpc.ServicerContext,
    ) -> pb.DernierIndexResponse:
        """Retourne le dernier index relevé pour un abonné (pour pré-remplissage)."""
        try:
            valeur = self._get_dernier_index_value(request.abonne_id)
            est_initial = valeur < 1e-9
            return pb.DernierIndexResponse(
                abonne_id=request.abonne_id,
                dernier_index=valeur,
                est_index_initial=est_initial,
            )
        except Exception as exc:
            logger.exception("GetDernierIndex échoué")
            context.abort(grpc.StatusCode.INTERNAL, str(exc))

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

    from django.conf import settings

    server = grpc.server(concurrent.futures.ThreadPoolExecutor(max_workers=10))
    pb_grpc.add_CampagneServiceServicer_to_server(CampagneServicer(), server)
    port = getattr(settings, "CAMPAGNE_GRPC_PORT", 50053)
    server.add_insecure_port(f"[::]:{port}")
    server.start()
    logger.info("Campagne gRPC server démarré sur le port %d", port)
    server.wait_for_termination()
