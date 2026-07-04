"""Queries GraphQL du Campagne Service."""

import strawberry
import strawberry.types

from .campagne_types import (
    Campagne,
    DernierIndex,
    Progression,
    Releve,
    ResumeCloture,
    campagne_from_grpc,
    releve_from_grpc,
)
from .context import require_auth, require_role
from .grpc_clients import campagne_client


def _verifier_acces_campagne(user: object, campagne_id: str) -> None:
    """Vérifie l'accès à une campagne selon le rôle :
    - SUPERVISEUR : doit en être le créateur.
    - AGENT : doit y être affecté.
    - ADMIN : accès libre (no-op).
    """
    role = getattr(user, "role", None)
    if role == "SUPERVISEUR":
        campagne = campagne_from_grpc(campagne_client.get_campagne(campagne_id))
        if campagne.created_by != user.user_id:
            raise PermissionError("Accès refusé : cette campagne ne vous appartient pas.")
    elif role == "AGENT":
        affectees = campagne_client.list_campagnes(agent_id=user.user_id)
        if campagne_id not in {c.campagne_id for c in affectees.campagnes}:
            raise PermissionError("Accès refusé : vous n'êtes pas affecté à cette campagne.")


# Alias conservé pour l'import dans campagne_mutations.py
_verifier_propriete_superviseur = _verifier_acces_campagne


@strawberry.type
class CampagneQueries:
    @strawberry.field
    def campagne(self, info: strawberry.types.Info, campagne_id: str) -> Campagne:
        """Détails d'une campagne — ADMIN (toutes), SUPERVISEUR (les siennes), AGENT (les siennes)."""
        user = require_auth(info)
        require_role(info, "ADMIN", "AGENT", "SUPERVISEUR")
        _verifier_acces_campagne(user, campagne_id)
        return campagne_from_grpc(campagne_client.get_campagne(campagne_id))

    @strawberry.field
    def campagnes(self, info: strawberry.types.Info) -> list[Campagne]:
        """
        Liste des campagnes.
        ADMIN : toutes.
        SUPERVISEUR : uniquement celles qu'il a créées.
        AGENT : uniquement celles auxquelles il est affecté.
        """
        user = require_auth(info)
        require_role(info, "ADMIN", "AGENT", "SUPERVISEUR")
        created_by = user.user_id if user.role == "SUPERVISEUR" else ""
        agent_id = user.user_id if user.role == "AGENT" else ""
        response = campagne_client.list_campagnes(created_by=created_by, agent_id=agent_id)
        return [campagne_from_grpc(c) for c in response.campagnes]

    @strawberry.field
    def releves(self, info: strawberry.types.Info, campagne_id: str) -> list[Releve]:
        """Liste des relevés d'une campagne — ADMIN, AGENT, SUPERVISEUR (les siennes)."""
        user = require_auth(info)
        require_role(info, "ADMIN", "AGENT", "SUPERVISEUR")
        _verifier_acces_campagne(user, campagne_id)
        response = campagne_client.list_releves(campagne_id)
        return [releve_from_grpc(r) for r in response.releves]

    @strawberry.field
    def progression(self, info: strawberry.types.Info, campagne_id: str) -> Progression:
        """Progression d'une campagne — ADMIN, AGENT, SUPERVISEUR (les siennes)."""
        user = require_auth(info)
        require_role(info, "ADMIN", "AGENT", "SUPERVISEUR")
        _verifier_acces_campagne(user, campagne_id)
        r = campagne_client.get_progression(campagne_id)
        return Progression(
            campagne_id=r.campagne_id,
            total_abonnes=r.total_abonnes,
            nb_releves=r.nb_releves,
            nb_en_attente=r.nb_en_attente,
            pourcentage=r.pourcentage,
        )

    @strawberry.field
    def resume_cloture(self, info: strawberry.types.Info, campagne_id: str) -> ResumeCloture:
        """Aperçu de clôture (ventilation des relevés + factures à générer) —
        ADMIN (toutes), SUPERVISEUR (les siennes)."""
        user = require_auth(info)
        require_role(info, "ADMIN", "SUPERVISEUR")
        _verifier_acces_campagne(user, campagne_id)
        r = campagne_client.get_resume_cloture(campagne_id)
        return ResumeCloture(
            campagne_id=r.campagne_id,
            total_abonnes=r.total_abonnes,
            nb_releves=r.nb_releves,
            nb_estimes=r.nb_estimes,
            nb_non_releves=r.nb_non_releves,
            nb_restants=r.nb_restants,
            nb_factures_a_generer=r.nb_factures_a_generer,
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
