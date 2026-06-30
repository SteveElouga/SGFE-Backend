"""Queries GraphQL du Campagne Service."""

import strawberry
import strawberry.types

from .campagne_types import Campagne, DernierIndex, Progression, Releve, campagne_from_grpc, releve_from_grpc
from .context import require_auth, require_role
from .grpc_clients import campagne_client


def _verifier_propriete_superviseur(user: object, campagne_id: str) -> None:
    """Lève PermissionError si un SUPERVISEUR tente d'accéder à une campagne qui n'est pas la sienne."""
    if getattr(user, "role", None) == "SUPERVISEUR":
        campagne = campagne_from_grpc(campagne_client.get_campagne(campagne_id))
        if campagne.created_by != user.user_id:
            raise PermissionError("Accès refusé : cette campagne ne vous appartient pas.")


@strawberry.type
class CampagneQueries:
    @strawberry.field
    def campagne(self, info: strawberry.types.Info, campagne_id: str) -> Campagne:
        """Détails d'une campagne — ADMIN et AGENT (toutes), SUPERVISEUR (les siennes)."""
        user = require_auth(info)
        require_role(info, "ADMIN", "AGENT", "SUPERVISEUR")
        response = campagne_client.get_campagne(campagne_id)
        campagne = campagne_from_grpc(response)
        if user.role == "SUPERVISEUR" and campagne.created_by != user.user_id:
            raise PermissionError("Accès refusé : cette campagne ne vous appartient pas.")
        return campagne

    @strawberry.field
    def campagnes(self, info: strawberry.types.Info) -> list[Campagne]:
        """
        Liste des campagnes.
        ADMIN et AGENT : toutes les campagnes.
        SUPERVISEUR : uniquement celles qu'il a créées.
        """
        user = require_auth(info)
        require_role(info, "ADMIN", "AGENT", "SUPERVISEUR")
        created_by = user.user_id if user.role == "SUPERVISEUR" else ""
        response = campagne_client.list_campagnes(created_by=created_by)
        return [campagne_from_grpc(c) for c in response.campagnes]

    @strawberry.field
    def releves(self, info: strawberry.types.Info, campagne_id: str) -> list[Releve]:
        """Liste des relevés d'une campagne — ADMIN, AGENT, SUPERVISEUR (les siennes)."""
        user = require_auth(info)
        require_role(info, "ADMIN", "AGENT", "SUPERVISEUR")
        _verifier_propriete_superviseur(user, campagne_id)
        response = campagne_client.list_releves(campagne_id)
        return [releve_from_grpc(r) for r in response.releves]

    @strawberry.field
    def progression(self, info: strawberry.types.Info, campagne_id: str) -> Progression:
        """Progression d'une campagne — ADMIN, AGENT, SUPERVISEUR (les siennes)."""
        user = require_auth(info)
        require_role(info, "ADMIN", "AGENT", "SUPERVISEUR")
        _verifier_propriete_superviseur(user, campagne_id)
        r = campagne_client.get_progression(campagne_id)
        return Progression(
            campagne_id=r.campagne_id,
            total_abonnes=r.total_abonnes,
            nb_releves=r.nb_releves,
            nb_en_attente=r.nb_en_attente,
            pourcentage=r.pourcentage,
        )

    @strawberry.field
    def dernier_index(self, info: strawberry.types.Info, abonne_id: str) -> DernierIndex:
        """Dernier index relevé pour un abonné — ADMIN, AGENT, SUPERVISEUR."""
        require_auth(info)
        require_role(info, "ADMIN", "AGENT", "SUPERVISEUR")
        r = campagne_client.get_dernier_index(abonne_id)
        return DernierIndex(
            abonne_id=r.abonne_id,
            dernier_index=r.dernier_index,
            est_index_initial=r.est_index_initial,
        )
