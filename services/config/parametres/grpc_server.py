import sys
from concurrent import futures
from pathlib import Path

import grpc
from django.conf import settings

sys.path.insert(0, str(Path(settings.BASE_DIR) / "proto"))

import config_service_pb2 as pb
import config_service_pb2_grpc as pb_grpc

from parametres.grpc_interceptors import ErrorHandlingInterceptor
from parametres.serializers import config_to_response, infos_to_response
from parametres.services import ConfigService, InfosSocieteService


class ConfigServiceServicer(pb_grpc.ConfigServiceServicer):
    """Implémentation des RPCs du Config Service.

    Les exceptions (ObjectDoesNotExist) sont gérées par ErrorHandlingInterceptor.
    """

    def __init__(self) -> None:
        self.infos_service = InfosSocieteService()
        self.config_service = ConfigService()

    def GetInfosSociete(self, request, context):
        infos = self.infos_service.get()
        return pb.InfosSocieteResponse(**infos_to_response(infos))

    def UpdateInfosSociete(self, request, context):
        infos = self.infos_service.update(
            nom=request.nom,
            adresse=request.adresse,
            telephone=request.telephone,
            logo_path=request.logo_path,
        )
        return pb.InfosSocieteResponse(**infos_to_response(infos))

    def GetConfig(self, request, context):
        param = self.config_service.get(request.cle)
        return pb.ConfigResponse(**config_to_response(param))

    def UpdateConfig(self, request, context):
        param = self.config_service.update(request.cle, request.valeur)
        return pb.ConfigResponse(**config_to_response(param))

    def ListConfigs(self, request, context):
        params = self.config_service.list_all()
        return pb.ListConfigsResponse(
            configs=[pb.ConfigResponse(**config_to_response(p)) for p in params]
        )


def serve() -> None:
    server = grpc.server(
        futures.ThreadPoolExecutor(max_workers=10),
        interceptors=[ErrorHandlingInterceptor()],
    )
    pb_grpc.add_ConfigServiceServicer_to_server(ConfigServiceServicer(), server)
    server.add_insecure_port(f"[::]:{settings.CONFIG_GRPC_PORT}")
    server.start()
    print(f"Config gRPC server démarré sur le port {settings.CONFIG_GRPC_PORT}")
    server.wait_for_termination()
