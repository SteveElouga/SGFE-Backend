"""Queries GraphQL du Facturation Service."""

import strawberry
import strawberry.types

from .context import require_auth, require_role
from .facturation_types import Facture, Tarif, facture_from_grpc, tarif_from_grpc
from .grpc_clients import abonne_client, campagne_client, facturation_client


def _abonnes_index() -> dict:
    """Index {abonne_id: AbonneResponse} via un seul ListAbonnes (best-effort)."""
    try:
        return {a.abonne_id: a for a in abonne_client.list_abonnes().abonnes}
    except Exception:
        return {}


def _campagnes_index() -> dict:
    """Index {campagne_id: CampagneResponse} via un seul ListCampagnes (best-effort)."""
    try:
        return {c.campagne_id: c for c in campagne_client.list_campagnes(created_by="", agent_id="").campagnes}
    except Exception:
        return {}


def _enrichir_factures(factures: list[Facture]) -> list[Facture]:
    """Complète chaque facture avec le nom de l'abonné et le nom/période de sa
    campagne (jointure best-effort côté Gateway). Les factures ne portent que
    `abonne_id`/`campagne_id` (règle « pas de FK inter-services ») ; on résout
    ici les libellés pour que les écrans factures/paiements s'affichent sans
    appeler `abonnes`/`campagnes`, réservées à d'autres rôles (le COMPTABLE n'a
    accès ni à Abonné ni à Campagne). Best-effort : si un service amont est
    indisponible, les libellés restent vides (jamais d'échec de la requête)."""
    if not factures:
        return factures
    abonnes = _abonnes_index()
    campagnes = _campagnes_index()
    for f in factures:
        abonne = abonnes.get(f.abonne_id)
        if abonne is not None:
            f.abonne_nom = f"{abonne.prenom} {abonne.nom}".strip()
            f.abonne_numero = abonne.numero_abonne
        campagne = campagnes.get(f.campagne_id)
        if campagne is not None:
            f.campagne_nom = campagne.nom
            f.campagne_periode_mois = campagne.periode_mois
            f.campagne_periode_annee = campagne.periode_annee
    return factures


@strawberry.type
class FacturationQueries:
    @strawberry.field
    def tarif_actuel(self, info: strawberry.types.Info) -> Tarif:
        """Tarif actif (prix du m³) — ADMIN, COMPTABLE, SUPERVISEUR.

        Le SUPERVISEUR le lit pour l'aperçu de clôture de ses campagnes (le tarif
        est affiché dans le récapitulatif avant génération des factures).
        """
        require_auth(info)
        require_role(info, "ADMIN", "COMPTABLE", "SUPERVISEUR")
        return tarif_from_grpc(facturation_client.get_tarif_actuel())

    @strawberry.field
    def facture(self, info: strawberry.types.Info, facture_id: str) -> Facture:
        """Détails d'une facture — ADMIN, COMPTABLE."""
        require_auth(info)
        require_role(info, "ADMIN", "COMPTABLE")
        return _enrichir_factures([facture_from_grpc(facturation_client.get_facture(facture_id))])[0]

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
        return _enrichir_factures([facture_from_grpc(f) for f in response.factures])

    @strawberry.field
    def factures_par_campagne(self, info: strawberry.types.Info, campagne_id: str) -> list[Facture]:
        """Toutes les factures d'une campagne — ADMIN, COMPTABLE."""
        require_auth(info)
        require_role(info, "ADMIN", "COMPTABLE")
        response = facturation_client.get_factures_par_campagne(campagne_id)
        return _enrichir_factures([facture_from_grpc(f) for f in response.factures])
