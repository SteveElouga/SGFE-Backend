"""Mutations GraphQL du Campagne Service."""

import strawberry
import strawberry.types

from proto import campagne_service_pb2 as campagne_pb

from .campagne_types import (
    AgentAffecte,
    Campagne,
    CorrigerReleveInput,
    CreateCampagneInput,
    MarquerNonReleveInput,
    Releve,
    SaisirIndexInput,
    ZoneInput,
    campagne_from_grpc,
    releve_from_grpc,
)
from .campagne_queries import _enrichir_agents, _verifier_propriete_superviseur
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
            numero_mobile_money=input.numero_mobile_money,
            generer_factures_auto=input.generer_factures_auto,
            envoyer_whatsapp_auto=input.envoyer_whatsapp_auto,
            demarrer_maintenant=input.demarrer_maintenant,
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
    def demarrer_campagne(self, info: strawberry.types.Info, campagne_id: str) -> Campagne:
        """Démarre une campagne PLANIFIEE (→ EN_COURS) — ADMIN (toutes), SUPERVISEUR (les siennes).

        Permet de lancer une campagne à la demande, sans attendre le cron 7h
        (qui ne démarre que les campagnes planifiées pour aujourd'hui/hier).
        """
        user = require_auth(info)
        require_role(info, "ADMIN", "SUPERVISEUR")
        _verifier_propriete_superviseur(user, campagne_id)
        response = campagne_client.demarrer_campagne(campagne_id)
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
            auteur_username=user.username,
            auteur_role=user.role,
        )
        return releve_from_grpc(response)

    @strawberry.mutation
    def corriger_releve(self, info: strawberry.types.Info, input: CorrigerReleveInput) -> Releve:
        """Corrige un index déjà relevé — ADMIN (tous), SUPERVISEUR (les siens).

        Autorisée même après clôture de la campagne (rectification d'erreur de
        saisie). Chaque correction est tracée dans le journal d'audit du relevé.
        """
        user = require_auth(info)
        require_role(info, "ADMIN", "SUPERVISEUR")
        _verifier_propriete_superviseur(user, input.campagne_id)
        response = campagne_client.corriger_releve(
            campagne_id=input.campagne_id,
            abonne_id=input.abonne_id,
            nouveau_index=input.nouveau_index,
            observation=input.observation,
            auteur_id=user.user_id,
            auteur_username=user.username,
            auteur_role=user.role,
        )
        return releve_from_grpc(response)

    @strawberry.mutation
    def affecter_zones(
        self,
        info: strawberry.types.Info,
        campagne_id: str,
        agent_id: str,
        zones: list[ZoneInput],
    ) -> list[AgentAffecte]:
        """Affecte un agent à un ensemble de zones (remplace ses zones actuelles).

        ADMIN (toutes), SUPERVISEUR (les siennes). Retourne la liste des agents
        de la campagne rafraîchie (avec zones, stats et statut de tournée).
        """
        user = require_auth(info)
        require_role(info, "ADMIN", "SUPERVISEUR")
        _verifier_propriete_superviseur(user, campagne_id)
        response = campagne_client.affecter_zones(
            campagne_id=campagne_id,
            agent_id=agent_id,
            zones=[campagne_pb.Zone(quartier=z.quartier, camp=z.camp) for z in zones],
        )
        return _enrichir_agents(response.agents)

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
