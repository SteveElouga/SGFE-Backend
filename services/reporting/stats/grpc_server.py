import sys
from concurrent import futures
from pathlib import Path

import grpc
from django.conf import settings

sys.path.insert(0, str(Path(settings.BASE_DIR) / "proto"))

import reporting_service_pb2 as pb
import reporting_service_pb2_grpc as pb_grpc

from stats.grpc_interceptors import ErrorHandlingInterceptor
from stats.grpc_auth import AuthServerInterceptor
from stats.services import AgregateurDashboard
from stats.serializers import (
    stats_campagne_to_dict,
    stats_facturation_to_dict,
    stats_paiements_to_dict,
)


class ReportingServiceServicer(pb_grpc.ReportingServiceServicer):
    """Implémentation des RPCs du Reporting Service (agrégateur read-only).

    ObjectDoesNotExist (GetStatsCampagne sur une campagne inconnue) est converti
    en NOT_FOUND par ErrorHandlingInterceptor.
    """

    def __init__(self) -> None:
        self._agg = AgregateurDashboard()

    def _dashboard_to_proto(self, d) -> pb.DashboardResponse:
        """Construit un DashboardResponse à partir d'un Dashboard (sous-blocs None → vides)."""
        return pb.DashboardResponse(
            campagne_en_cours=(
                pb.StatsCampagneResponse(**stats_campagne_to_dict(d.campagne))
                if d.campagne is not None
                else pb.StatsCampagneResponse()
            ),
            facturation_en_cours=(
                pb.StatsFacturationResponse(**stats_facturation_to_dict(d.facturation))
                if d.facturation is not None
                else pb.StatsFacturationResponse()
            ),
            paiements_en_cours=(
                pb.StatsPaiementsResponse(**stats_paiements_to_dict(d.paiements))
                if d.paiements is not None
                else pb.StatsPaiementsResponse()
            ),
        )

    def GetDashboard(self, request, context):
        return self._dashboard_to_proto(self._agg.get_dashboard())

    def GetStatsCompletes(self, request, context):
        return self._dashboard_to_proto(self._agg.get_stats_completes(request.campagne_id))

    def GetStatsCampagne(self, request, context):
        stats = self._agg.get_stats_campagne(request.campagne_id)
        return pb.StatsCampagneResponse(**stats_campagne_to_dict(stats))

    def GetStatsGlobales(self, request, context):
        g = self._agg.get_stats_globales()
        return pb.StatsGlobalesResponse(
            historique_campagnes=[
                pb.StatsCampagneResponse(**stats_campagne_to_dict(c)) for c in g.historique_campagnes
            ],
            consommation_totale_globale=float(g.consommation_totale_globale),
            montant_total_facture_global=float(g.montant_total_facture_global),
            montant_total_encaisse_global=float(g.montant_total_encaisse_global),
        )

    def UpdateStatsCampagne(self, request, context):
        self._agg.update_stats_campagne(
            campagne_id=request.campagne_id,
            nom_campagne=request.nom_campagne,
            total_abonnes=request.total_abonnes,
            nb_releves=request.nb_releves,
            consommation_totale=request.consommation_totale,
        )
        return pb.StatusResponse(success=True)

    def UpdateStatsFacturation(self, request, context):
        self._agg.update_stats_facturation(
            campagne_id=request.campagne_id,
            delta_factures=request.delta_factures,
            delta_montant=request.delta_montant,
            type_update=request.type_update,
        )
        return pb.StatusResponse(success=True)

    def UpdateStatsPaiements(self, request, context):
        self._agg.update_stats_paiements(
            campagne_id=request.campagne_id,
            montant_paiement=request.montant_paiement,
            type_update=request.type_update,
        )
        return pb.StatusResponse(success=True)


def serve() -> None:
    server = grpc.server(
        futures.ThreadPoolExecutor(max_workers=10),
        interceptors=[AuthServerInterceptor(settings.INTERNAL_GRPC_KEY), ErrorHandlingInterceptor()],
    )
    pb_grpc.add_ReportingServiceServicer_to_server(ReportingServiceServicer(), server)
    server.add_insecure_port(f"[::]:{settings.REPORTING_GRPC_PORT}")
    server.start()
    # Alimente le read model : consomme le flux Redis d'événements dans un
    # thread daemon (best-effort — n'empêche pas le démarrage si Redis est down).
    from stats.event_consumer import start_consumer_thread

    start_consumer_thread()
    print(f"Reporting gRPC server démarré sur le port {settings.REPORTING_GRPC_PORT}")
    server.wait_for_termination()
