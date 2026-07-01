"""Queries GraphQL du Paiement Service."""

import strawberry
import strawberry.types

from .context import require_auth, require_role
from .grpc_clients import paiement_client
from .paiement_types import SoldeFacture, Paiement, SuiviImpaye, paiement_from_grpc, solde_from_grpc, suivi_from_grpc


@strawberry.type
class PaiementQueries:
    @strawberry.field
    def solde_facture(self, info: strawberry.types.Info, facture_id: str) -> SoldeFacture:
        """Solde d'une facture (montant total, payé, restant) — ADMIN, COMPTABLE."""
        require_auth(info)
        require_role(info, "ADMIN", "COMPTABLE")
        return solde_from_grpc(paiement_client.get_solde(facture_id))

    @strawberry.field
    def paiements(
        self,
        info: strawberry.types.Info,
        facture_id: str = "",
        abonne_id: str = "",
    ) -> list[Paiement]:
        """Liste des paiements avec filtres optionnels — ADMIN, COMPTABLE."""
        require_auth(info)
        require_role(info, "ADMIN", "COMPTABLE")
        response = paiement_client.list_paiements(facture_id=facture_id, abonne_id=abonne_id)
        return [paiement_from_grpc(p) for p in response.paiements]

    @strawberry.field
    def impayes(self, info: strawberry.types.Info) -> list[SoldeFacture]:
        """Liste des factures impayées (date limite dépassée) — ADMIN, COMPTABLE."""
        require_auth(info)
        require_role(info, "ADMIN", "COMPTABLE")
        response = paiement_client.list_impayes()
        return [solde_from_grpc(s) for s in response.impayes]

    @strawberry.field
    def suivi_impaye(self, info: strawberry.types.Info, facture_id: str) -> SuiviImpaye:
        """Détail du suivi de relance pour une facture impayée — ADMIN, COMPTABLE."""
        require_auth(info)
        require_role(info, "ADMIN", "COMPTABLE")
        return suivi_from_grpc(paiement_client.get_suivi_impaye(facture_id))
