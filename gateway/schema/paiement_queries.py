"""Queries GraphQL du Paiement Service."""

import logging

import strawberry
import strawberry.types

from .context import require_auth, require_role
from .grpc_clients import auth_client, paiement_client
from .paiement_types import (
    Avoir,
    DetteAbonne,
    Paiement,
    SoldeFacture,
    SuiviImpaye,
    avoir_from_grpc,
    paiement_from_grpc,
    solde_from_grpc,
    suivi_from_grpc,
)

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
        limit: int | None = None,
        offset: int | None = None,
    ) -> list[Paiement]:
        """Liste des paiements avec filtres optionnels — ADMIN, COMPTABLE.

        `limit`/`offset` optionnels : omis, la liste complète filtrée est
        renvoyée à l'identique — comportement historique préservé
        (rétrocompatibilité stricte). Voir `paiementsCount` pour le nombre
        total sans charger la liste.
        """
        require_auth(info)
        require_role(info, "ADMIN", "COMPTABLE")
        pagination: dict[str, int] = {}
        if limit is not None:
            pagination["limit"] = limit
        if offset is not None:
            pagination["offset"] = offset
        response = paiement_client.list_paiements(facture_id=facture_id, abonne_id=abonne_id, **pagination)
        operateurs = _resoudre_operateurs({p.enregistre_par for p in response.paiements})
        return [paiement_from_grpc(p, operateur=operateurs.get(p.enregistre_par, "")) for p in response.paiements]

    @strawberry.field
    def paiements_count(self, info: strawberry.types.Info, facture_id: str = "", abonne_id: str = "") -> int:
        """Nombre total de paiements correspondant aux filtres — ADMIN, COMPTABLE.

        Choix technique (voir le rapport de la tâche « pagination serveur ») :
        une query dédiée plutôt qu'un champ `total` sur `paiements`, pour ne
        pas changer le type de retour existant (`[Paiement!]!`).
        """
        require_auth(info)
        require_role(info, "ADMIN", "COMPTABLE")
        return paiement_client.count_paiements(facture_id=facture_id, abonne_id=abonne_id)

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

    @strawberry.field
    def dette_abonne(
        self,
        info: strawberry.types.Info,
        abonne_id: str,
        hors_facture_id: str | None = None,
    ) -> DetteAbonne:
        """Ce qu'un abonné doit encore, toutes factures confondues — ADMIN, COMPTABLE.

        `horsFactureId` sert à l'impression : sur une facture, le « solde
        antérieur » est ce que l'abonné doit EN PLUS de celle qu'il tient en
        main.
        """
        require_auth(info)
        require_role(info, "ADMIN", "COMPTABLE")
        r = paiement_client.get_dette_abonne(abonne_id, hors_facture_id or "")
        return DetteAbonne(
            total_du=r.total_du,
            nb_factures=r.nb_factures,
            plus_ancienne_echeance=r.plus_ancienne_echeance or None,
        )

    @strawberry.field
    def avoir_abonne(self, info: strawberry.types.Info, abonne_id: str) -> Avoir:
        """Solde d'avoir (crédit) d'un abonné + journal des mouvements — ADMIN, COMPTABLE."""
        require_auth(info)
        require_role(info, "ADMIN", "COMPTABLE")
        return avoir_from_grpc(paiement_client.get_avoir_abonne(abonne_id))
