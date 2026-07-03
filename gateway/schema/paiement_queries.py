"""Queries GraphQL du Paiement Service."""

import logging

import strawberry
import strawberry.types

from .context import require_auth, require_role
from .grpc_clients import auth_client, paiement_client
from .paiement_types import SoldeFacture, Paiement, SuiviImpaye, paiement_from_grpc, solde_from_grpc, suivi_from_grpc

logger = logging.getLogger(__name__)


def _resoudre_operateurs(user_ids: set[str]) -> dict[str, str]:
    """Résout un lot d'user_id (enregistre_par) en noms d'utilisateur affichables.

    Un appel gRPC par utilisateur *distinct* (pas par paiement — en pratique
    un petit nombre de comptables/admins enregistrent l'essentiel des
    versements). Dégradation gracieuse : un utilisateur non résolu retombe
    sur un repli basé sur son identifiant plutôt que de faire échouer la liste.
    """
    resolus: dict[str, str] = {}
    for user_id in user_ids:
        if not user_id:
            continue
        try:
            resolus[user_id] = auth_client.get_user(user_id).username
        except Exception as exc:
            logger.warning("Impossible de résoudre l'opérateur %s — repli sur l'identifiant", user_id, exc_info=exc)
            resolus[user_id] = f"Utilisateur {user_id[:8]}"
    return resolus


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
        operateurs = _resoudre_operateurs({p.enregistre_par for p in response.paiements})
        return [paiement_from_grpc(p, operateur=operateurs.get(p.enregistre_par, "")) for p in response.paiements]

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
