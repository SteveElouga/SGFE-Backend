import sys
from concurrent import futures
from pathlib import Path

import grpc
from django.conf import settings

sys.path.insert(0, str(Path(settings.BASE_DIR) / "proto"))

import config_service_pb2 as pb
import config_service_pb2_grpc as pb_grpc

from parametres.cache import (
    get_cached_infos_societe,
    get_cached_param,
    invalidate_infos_societe,
    invalidate_param,
    set_cached_infos_societe,
    set_cached_param,
)
from parametres.event_publisher import publish_config_event
from parametres.grpc_interceptors import ErrorHandlingInterceptor
from parametres.grpc_auth import AuthServerInterceptor, ouvrir_port_grpc
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
        # Lu à chaque reçu de paiement généré côté Facturation (RecuPaiementService)
        # et à chaque lot de factures — cache court, invalidé explicitement par
        # UpdateInfosSociete (voir parametres/cache.py).
        cached = get_cached_infos_societe()
        if cached is not None:
            return pb.InfosSocieteResponse(**cached)
        infos = self.infos_service.get()
        data = infos_to_response(infos)
        set_cached_infos_societe(data)
        return pb.InfosSocieteResponse(**data)

    def UpdateInfosSociete(self, request, context):
        infos = self.infos_service.update(
            nom=request.nom,
            adresse=request.adresse,
            telephone=request.telephone,
            logo_path=request.logo_path,
        )
        invalidate_infos_societe()
        return pb.InfosSocieteResponse(**infos_to_response(infos))

    def GetConfig(self, request, context):
        # Lu à chaque escalade de relance impayé (Paiement Service) et à chaque
        # génération de facture — cache court, invalidé explicitement par
        # UpdateConfig (voir parametres/cache.py).
        cached = get_cached_param(request.cle)
        if cached is not None:
            return pb.ConfigResponse(**cached)
        param = self.config_service.get(request.cle)
        data = config_to_response(param)
        set_cached_param(request.cle, data)
        return pb.ConfigResponse(**data)

    def UpdateConfig(self, request, context):
        param = self.config_service.update(request.cle, request.valeur)
        invalidate_param(param.cle)
        publish_config_event(param.cle, "CONFIG_UPDATED")
        return pb.ConfigResponse(**config_to_response(param))

    def ListConfigs(self, request, context):
        params = self.config_service.list_all()
        return pb.ListConfigsResponse(configs=[pb.ConfigResponse(**config_to_response(p)) for p in params])


def serve() -> None:
    server = grpc.server(
        futures.ThreadPoolExecutor(max_workers=10),
        interceptors=[AuthServerInterceptor(settings.INTERNAL_GRPC_KEY), ErrorHandlingInterceptor()],
    )
    pb_grpc.add_ConfigServiceServicer_to_server(ConfigServiceServicer(), server)
    ouvrir_port_grpc(server, settings.CONFIG_GRPC_PORT)
    server.start()
    print(f"Config gRPC server démarré sur le port {settings.CONFIG_GRPC_PORT}")
    server.wait_for_termination()
