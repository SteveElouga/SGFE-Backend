import sys
from concurrent import futures
from pathlib import Path

import grpc
from django.conf import settings
from django.core.exceptions import ObjectDoesNotExist

sys.path.insert(0, str(Path(settings.BASE_DIR) / "proto"))

import abonne_service_pb2 as pb
import abonne_service_pb2_grpc as pb_grpc

from abonnes.event_publisher import publish_abonne_event
from abonnes.grpc_interceptors import ErrorHandlingInterceptor
from abonnes.serializers import abonne_to_response, compteur_to_response, historique_to_response
from abonnes.services import AbonneService, CompteurService


class AbonneServiceServicer(pb_grpc.AbonneServiceServicer):
    """Les exceptions (ValidationError, ObjectDoesNotExist, IntegrityError)
    ne sont pas interceptées ici : ErrorHandlingInterceptor s'en charge de
    façon centralisée (voir grpc_interceptors.py).
    """

    def __init__(self) -> None:
        self.abonne_service = AbonneService()
        self.compteur_service = CompteurService()

    def _response(self, abonne) -> pb.AbonneResponse:
        try:
            compteur = self.compteur_service.get_compteur_actif(str(abonne.id))
        except ObjectDoesNotExist:
            compteur = None
        data = abonne_to_response(abonne, compteur)
        compteur_data = data.pop("compteur")
        return pb.AbonneResponse(
            **data,
            compteur=pb.CompteurResponse(**compteur_data) if compteur_data else None,
        )

    def GetAbonne(self, request, context):
        abonne = self.abonne_service.get_abonne(request.abonne_id)
        return self._response(abonne)

    def ListAbonnes(self, request, context):
        abonnes = self.abonne_service.list_abonnes(request.statut or None)
        return pb.ListAbonnesResponse(abonnes=[self._response(a) for a in abonnes])

    def ListAbonnesActifs(self, request, context):
        abonnes = self.abonne_service.list_abonnes_actifs()
        return pb.ListAbonnesResponse(abonnes=[self._response(a) for a in abonnes])

    def CreateAbonne(self, request, context):
        abonne = self.abonne_service.create_abonne(
            nom=request.nom,
            prenom=request.prenom,
            telephone_whatsapp=request.telephone_whatsapp,
            adresse=request.adresse,
            numero_compteur=request.numero_compteur,
            quartier=request.quartier,
            camp=request.camp,
            index_initial=request.index_initial,
            date_pose=request.date_pose,
        )
        publish_abonne_event(str(abonne.id), "ABONNE_CREATED")
        return self._response(abonne)

    def UpdateAbonne(self, request, context):
        abonne = self.abonne_service.update_abonne(
            abonne_id=request.abonne_id,
            nom=request.nom,
            prenom=request.prenom,
            telephone_whatsapp=request.telephone_whatsapp,
            adresse=request.adresse,
        )
        publish_abonne_event(str(abonne.id))
        return self._response(abonne)

    def SuspendreAbonne(self, request, context):
        abonne = self.abonne_service.suspendre_abonne(request.abonne_id)
        publish_abonne_event(str(abonne.id))
        return self._response(abonne)

    def ReactiverAbonne(self, request, context):
        abonne = self.abonne_service.reactiver_abonne(request.abonne_id)
        publish_abonne_event(str(abonne.id))
        return self._response(abonne)

    def ResilierAbonne(self, request, context):
        abonne = self.abonne_service.resilier_abonne(request.abonne_id)
        publish_abonne_event(str(abonne.id))
        return self._response(abonne)

    def GetCompteur(self, request, context):
        compteur = self.compteur_service.get_compteur_actif(request.abonne_id)
        return pb.CompteurResponse(**compteur_to_response(compteur))

    def UpdateCompteur(self, request, context):
        compteur = self.compteur_service.update_compteur(
            abonne_id=request.abonne_id,
            quartier=request.quartier if request.HasField("quartier") else None,
            camp=request.camp if request.HasField("camp") else None,
            index_initial=request.index_initial if request.HasField("index_initial") else None,
            date_pose=request.date_pose if request.HasField("date_pose") else None,
        )
        publish_abonne_event(request.abonne_id)
        return pb.CompteurResponse(**compteur_to_response(compteur))

    def GetHistoriqueCompteur(self, request, context):
        historique = self.compteur_service.get_historique(request.abonne_id)
        items = []
        for h in historique:
            data = historique_to_response(h)
            items.append(
                pb.HistoriqueCompteurResponse(
                    historique_id=data["historique_id"],
                    ancien_compteur=pb.CompteurResponse(**data["ancien_compteur"]),
                    nouveau_compteur=pb.CompteurResponse(**data["nouveau_compteur"]),
                    index_fermeture=data["index_fermeture"],
                    date_remplacement=data["date_remplacement"],
                    created_at=data["created_at"],
                )
            )
        return pb.ListHistoriqueResponse(historique=items)

    def RemplacerCompteur(self, request, context):
        compteur = self.compteur_service.remplacer_compteur(
            abonne_id=request.abonne_id,
            index_fermeture=request.index_fermeture,
            nouveau_numero_compteur=request.nouveau_numero_compteur,
            nouveau_quartier=request.nouveau_quartier,
            nouveau_camp=request.nouveau_camp,
            nouvel_index_initial=request.nouvel_index_initial,
            date_remplacement=request.date_remplacement,
        )
        publish_abonne_event(request.abonne_id)
        return pb.CompteurResponse(**compteur_to_response(compteur))


def serve() -> None:
    server = grpc.server(futures.ThreadPoolExecutor(max_workers=10), interceptors=[ErrorHandlingInterceptor()])
    pb_grpc.add_AbonneServiceServicer_to_server(AbonneServiceServicer(), server)
    server.add_insecure_port(f"[::]:{settings.ABONNE_GRPC_PORT}")
    server.start()
    print(f"Abonné gRPC server démarré sur le port {settings.ABONNE_GRPC_PORT}")
    server.wait_for_termination()
