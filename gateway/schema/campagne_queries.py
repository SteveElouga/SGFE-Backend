"""Queries GraphQL du Campagne Service."""

import strawberry
import strawberry.types

from .campagne_types import Campagne, DernierIndex, Progression, Releve, campagne_from_grpc, releve_from_grpc
from .context import require_auth, require_role
from .grpc_clients import campagne_client


@strawberry.type
class CampagneQueries:
    @strawberry.field
    def campagne(self, info: strawberry.types.Info, campagne_id: str) -> Campagne:
        """Détails d'une campagne — ADMIN, SUPERVISEUR (filtre handled by ListCampagnes)."""
        require_auth(info)
        require_role(info, "ADMIN", "SUPERVISEUR")
        response = campagne_client.get_campagne(campagne_id)
        return campagne_from_grpc(response)

    @strawberry.field
    def campagnes(self, info: strawberry.types.Info) -> list[Campagne]:
        """
        Liste des campagnes.
        ADMIN : toutes les campagnes.
        SUPERVISEUR : uniquement celles qu'il a créées.
        """
        user = require_auth(info)
        require_role(info, "ADMIN", "SUPERVISEUR")
        created_by = "" if user.role == "ADMIN" else user.user_id
        response = campagne_client.list_campagnes(created_by=created_by)
        return [campagne_from_grpc(c) for c in response.campagnes]

    @strawberry.field
    def releves(self, info: strawberry.types.Info, campagne_id: str) -> list[Releve]:
        """Liste des relevés d'une campagne — ADMIN, AGENT, SUPERVISEUR."""
        require_auth(info)
        require_role(info, "ADMIN", "AGENT", "SUPERVISEUR")
        response = campagne_client.list_releves(campagne_id)
        return [releve_from_grpc(r) for r in response.releves]

    @strawberry.field
    def progression(self, info: strawberry.types.Info, campagne_id: str) -> Progression:
        """Progression d'une campagne — ADMIN, AGENT, SUPERVISEUR."""
        require_auth(info)
        require_role(info, "ADMIN", "AGENT", "SUPERVISEUR")
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
