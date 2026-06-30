"""Mutations GraphQL du Campagne Service."""

import strawberry
import strawberry.types

from .campagne_types import (
    Campagne,
    CreateCampagneInput,
    MarquerNonReleveInput,
    Releve,
    SaisirIndexInput,
    campagne_from_grpc,
    releve_from_grpc,
)
from .campagne_queries import _verifier_propriete_superviseur
from .context import require_auth, require_role
from .grpc_clients import campagne_client


@strawberry.type
class CampagneMutations:
    @strawberry.mutation
    def creer_campagne(self, info: strawberry.types.Info, input: CreateCampagneInput) -> Campagne:
        """Crée une nouvelle campagne — ADMIN ou SUPERVISEUR."""
        user = require_auth(info)
        require_role(info, "ADMIN", "SUPERVISEUR")
        response = campagne_client.create_campagne(
            nom=input.nom,
            periode_mois=input.periode_mois,
            periode_annee=input.periode_annee,
            date_planifiee=input.date_planifiee,
            created_by=user.user_id,
        )
        return campagne_from_grpc(response)

    @strawberry.mutation
    def affecter_agent(self, info: strawberry.types.Info, campagne_id: str, agent_id: str) -> Campagne:
        """Affecte un AGENT à une campagne — ADMIN (toutes), SUPERVISEUR (les siennes)."""
        user = require_auth(info)
        require_role(info, "ADMIN", "SUPERVISEUR")
        _verifier_propriete_superviseur(user, campagne_id)
        response = campagne_client.assigner_agent(campagne_id=campagne_id, agent_id=agent_id)
        return campagne_from_grpc(response)

    @strawberry.mutation
    def cloturer_campagne(self, info: strawberry.types.Info, campagne_id: str) -> Campagne:
        """Clôture une campagne EN_COURS — ADMIN (toutes), SUPERVISEUR (les siennes)."""
        user = require_auth(info)
        require_role(info, "ADMIN", "SUPERVISEUR")
        _verifier_propriete_superviseur(user, campagne_id)
        response = campagne_client.cloturer_campagne(campagne_id)
        return campagne_from_grpc(response)

    @strawberry.mutation
    def saisir_index(self, info: strawberry.types.Info, input: SaisirIndexInput) -> Releve:
        """Saisit le nouvel index d'un abonné — ADMIN, AGENT, SUPERVISEUR (les siennes)."""
        user = require_auth(info)
        require_role(info, "ADMIN", "AGENT", "SUPERVISEUR")
        _verifier_propriete_superviseur(user, input.campagne_id)
        response = campagne_client.saisir_index(
            campagne_id=input.campagne_id,
            abonne_id=input.abonne_id,
            nouveau_index=input.nouveau_index,
            observation=input.observation,
            agent_id=user.user_id,
        )
        return releve_from_grpc(response)

    @strawberry.mutation
    def marquer_non_releve(self, info: strawberry.types.Info, input: MarquerNonReleveInput) -> Releve:
        """Marque un relevé comme NON_RELEVE ou ESTIME — ADMIN, AGENT, SUPERVISEUR (les siennes)."""
        user = require_auth(info)
        require_role(info, "ADMIN", "AGENT", "SUPERVISEUR")
        _verifier_propriete_superviseur(user, input.campagne_id)
        response = campagne_client.marquer_non_releve(
            campagne_id=input.campagne_id,
            abonne_id=input.abonne_id,
            statut=input.statut,
            observation=input.observation,
            agent_id=user.user_id,
        )
        return releve_from_grpc(response)
