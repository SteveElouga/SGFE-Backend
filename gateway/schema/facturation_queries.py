"""Queries GraphQL du Facturation Service."""

import strawberry
import strawberry.types

from .context import require_auth, require_role
from .facturation_types import Facture, Tarif, facture_from_grpc, tarif_from_grpc
from .grpc_clients import facturation_client


@strawberry.type
class FacturationQueries:
    @strawberry.field
    def tarif_actuel(self, info: strawberry.types.Info) -> Tarif:
        """Tarif actif (prix du m³) — ADMIN, COMPTABLE."""
        require_auth(info)
        require_role(info, "ADMIN", "COMPTABLE")
        return tarif_from_grpc(facturation_client.get_tarif_actuel())

    @strawberry.field
    def facture(self, info: strawberry.types.Info, facture_id: str) -> Facture:
        """Détails d'une facture — ADMIN, COMPTABLE."""
        require_auth(info)
        require_role(info, "ADMIN", "COMPTABLE")
        return facture_from_grpc(facturation_client.get_facture(facture_id))

    @strawberry.field
    def factures(
        self,
        info: strawberry.types.Info,
        campagne_id: str = "",
        abonne_id: str = "",
        statut: str = "",
    ) -> list[Facture]:
        """Liste des factures avec filtres optionnels — ADMIN, COMPTABLE."""
        require_auth(info)
        require_role(info, "ADMIN", "COMPTABLE")
        response = facturation_client.list_factures(campagne_id=campagne_id, abonne_id=abonne_id, statut=statut)
        return [facture_from_grpc(f) for f in response.factures]

    @strawberry.field
    def factures_par_campagne(self, info: strawberry.types.Info, campagne_id: str) -> list[Facture]:
        """Toutes les factures d'une campagne — ADMIN, COMPTABLE."""
        require_auth(info)
        require_role(info, "ADMIN", "COMPTABLE")
        response = facturation_client.get_factures_par_campagne(campagne_id)
        return [facture_from_grpc(f) for f in response.factures]
