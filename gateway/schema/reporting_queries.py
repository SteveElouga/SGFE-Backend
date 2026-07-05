"""Queries GraphQL du Reporting Service — tableau de bord (ADMIN, COMPTABLE)."""

import strawberry
import strawberry.types

from schema.context import require_role
from schema.grpc_clients import reporting_client
from schema.reporting_types import (
    Dashboard,
    StatsCampagne,
    StatsGlobales,
    dashboard_from_grpc,
    stats_campagne_from_grpc,
    stats_globales_from_grpc,
)


@strawberry.type
class ReportingQueries:
    @strawberry.field
    def dashboard(self, info: strawberry.types.Info) -> Dashboard:
        """Tableau de bord de la campagne en cours (stats pré-calculées) — ADMIN, COMPTABLE."""
        require_role(info, "ADMIN", "COMPTABLE")
        return dashboard_from_grpc(reporting_client.get_dashboard())

    @strawberry.field
    def stats_campagne(self, info: strawberry.types.Info, campagne_id: str) -> StatsCampagne:
        """Statistiques de relevé d'une campagne — ADMIN, COMPTABLE."""
        require_role(info, "ADMIN", "COMPTABLE")
        return stats_campagne_from_grpc(reporting_client.get_stats_campagne(campagne_id))

    @strawberry.field
    def stats_globales(self, info: strawberry.types.Info) -> StatsGlobales:
        """Statistiques globales tous exercices (historique + totaux) — ADMIN, COMPTABLE."""
        require_role(info, "ADMIN", "COMPTABLE")
        return stats_globales_from_grpc(reporting_client.get_stats_globales())
