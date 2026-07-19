"""Queries GraphQL du Campagne Service."""

import strawberry
import strawberry.types

from .campagne_types import (
    AgentAffecte,
    Campagne,
    DernierIndex,
    Progression,
    Releve,
    ResumeCloture,
    ZoneDisponible,
    ZoneRepartition,
    ZoneStat,
    campagne_from_grpc,
    releve_from_grpc,
)
from .context import require_auth, require_role
from .grpc_clients import abonne_client, auth_client, campagne_client


def _verifier_acces_campagne(user: object, campagne_id: str) -> None:
    """Vérifie l'accès à une campagne selon le rôle :
    - SUPERVISEUR : doit en être le créateur.
    - AGENT : doit y être affecté.
    - ADMIN : accès libre (no-op).
    """
    role = getattr(user, "role", None)
    if role == "SUPERVISEUR":
        # On lit created_by directement sur la réponse gRPC (CampagneResponse)
        # plutôt que via le type GraphQL Campagne : le champ reste interne et
        # n'est pas exposé dans le schéma public (aucun impact frontend).
        campagne = campagne_client.get_campagne(campagne_id)
        if campagne.created_by != user.user_id:
            raise PermissionError("Accès refusé : cette campagne ne vous appartient pas.")
    elif role == "AGENT":
        affectees = campagne_client.list_campagnes(agent_id=user.user_id)
        if campagne_id not in {c.campagne_id for c in affectees.campagnes}:
            raise PermissionError("Accès refusé : vous n'êtes pas affecté à cette campagne.")


# Alias conservé pour l'import dans campagne_mutations.py
_verifier_propriete_superviseur = _verifier_acces_campagne


def _statut_tournee(derniere_activite: str) -> str:
    """Dérive le statut de tournée d'un agent depuis la date de son dernier relevé.

    Pas de heartbeat dédié : la saisie étant temps réel, « dernière activité »
    = date du dernier relevé. Seuils : ≤15 min → EN_TOURNEE, ≤2 h → ACTIF,
    au-delà → EN_RETARD ; aucun relevé → INACTIF.
    """
    if not derniere_activite:
        return "INACTIF"
    from datetime import datetime, timezone

    try:
        dt = datetime.fromisoformat(derniere_activite)
    except ValueError:
        return "ACTIF"
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    minutes = (datetime.now(timezone.utc) - dt).total_seconds() / 60
    if minutes <= 15:
        return "EN_TOURNEE"
    if minutes <= 120:
        return "ACTIF"
    return "EN_RETARD"


def _users_par_id() -> dict:
    """Index {user_id: UserResponse} via UN seul appel ListUsers (best-effort).

    Évite le N+1 : un GetUser par agent devient un unique ListUsers, indexé en
    mémoire. Enrichissement non bloquant — dict vide si Auth est indisponible."""
    try:
        return {u.user_id: u for u in auth_client.list_users().users}
    except Exception:
        return {}


def _abonnes_par_id() -> dict:
    """Index {abonne_id: AbonneResponse} via UN seul appel ListAbonnes (best-effort).

    Même idiome anti-N+1 que `_users_par_id` : un `GetAbonne` par relevé
    deviendrait N appels ; on récupère tous les abonnés une fois et on indexe en
    mémoire. Dict vide si Abonné Service est indisponible."""
    try:
        return {a.abonne_id: a for a in abonne_client.list_abonnes().abonnes}
    except Exception:
        return {}


def _enrichir_releves(releves: list[Releve]) -> list[Releve]:
    """Complète chaque relevé avec l'identité de l'abonné (nom, prénom, numéro,
    adresse, numéro de compteur) issue d'Abonné Service, pour que l'écran affiche
    des noms et non des UUID. Le relevé ne porte que `abonne_id` (règle « pas de
    FK inter-services ») : la jointure se fait ici, côté Gateway. Best-effort —
    si Abonné est indisponible, les champs restent vides et le relevé est renvoyé
    tel quel (jamais d'échec de la requête)."""
    if not releves:
        return releves
    abonnes = _abonnes_par_id()
    for releve in releves:
        abonne = abonnes.get(releve.abonne_id)
        if abonne is None:
            continue
        releve.abonne_nom = abonne.nom
        releve.abonne_prenom = abonne.prenom
        releve.numero_abonne = abonne.numero_abonne
        releve.abonne_adresse = abonne.adresse
        releve.numero_compteur = abonne.compteur.numero_compteur
    return releves


def _enrichir_agents(grpc_agents) -> list[AgentAffecte]:
    """Complète les agents (issus de ListAgentsCampagne) avec le nombre d'abonnés
    par zone (ListZones), le nom/rôle (Auth) et le statut de tournée dérivé."""
    zones_abonnes = {(z.quartier, z.camp): z.nb_abonnes for z in abonne_client.list_zones().zones}
    users_by_id = _users_par_id()
    agents: list[AgentAffecte] = []
    for a in grpc_agents:
        user = users_by_id.get(a.agent_id)
        username = user.username if user else ""
        role = user.role if user else ""
        zones = []
        for z in a.zones:
            nb_ab = zones_abonnes.get((z.quartier, z.camp), 0)
            zones.append(
                ZoneStat(
                    quartier=z.quartier,
                    camp=z.camp,
                    nb_abonnes=nb_ab,
                    nb_releves=z.nb_releves,
                    pct=round(z.nb_releves / nb_ab * 100, 1) if nb_ab else 0.0,
                )
            )
        agents.append(
            AgentAffecte(
                agent_id=a.agent_id,
                username=username,
                role=role,
                statut=_statut_tournee(a.derniere_activite),
                derniere_activite=a.derniere_activite,
                nb_releves=a.nb_releves,
                zones=zones,
            )
        )
    return agents


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
        return _enrichir_releves([releve_from_grpc(r) for r in response.releves])

    @strawberry.field
    def releves_par_agent(self, info: strawberry.types.Info, campagne_id: str, agent_id: str) -> list[Releve]:
        """Tournée d'un agent dans une campagne (écran « tournée agent »).

        Renvoie ses relevés **déjà saisis** **+** les abonnés **à relever** de son
        périmètre (ses **zones** ; ou **toute la campagne** s'il n'a aucune zone
        affectée). Sans les A_RELEVER, l'agent ne verrait jamais ce qu'il doit
        relever (ceux-ci n'ont d'`agent_id` qu'après saisie).

        ADMIN (toutes), SUPERVISEUR (les siennes), AGENT (sa propre tournée
        uniquement). Le périmètre (zones vs global) est résolu par campagne-service
        (`ListRelevesTournee`), qui a accès aux affectations de zones.
        """
        user = require_auth(info)
        require_role(info, "ADMIN", "AGENT", "SUPERVISEUR")
        _verifier_acces_campagne(user, campagne_id)
        if user.role == "AGENT" and agent_id != user.user_id:
            raise PermissionError("Accès refusé : vous ne pouvez consulter que votre propre tournée.")
        response = campagne_client.list_releves_tournee(campagne_id, agent_id)
        return _enrichir_releves([releve_from_grpc(r) for r in response.releves])

    @strawberry.field
    def agents_campagne(self, info: strawberry.types.Info, campagne_id: str) -> list[AgentAffecte]:
        """Agents affectés à une campagne (cartes « détail campagne ») : zones,
        avancement, statut de tournée et dernière activité.

        ADMIN (toutes), SUPERVISEUR (les siennes), AGENT (les siennes).
        """
        user = require_auth(info)
        require_role(info, "ADMIN", "AGENT", "SUPERVISEUR")
        _verifier_acces_campagne(user, campagne_id)
        response = campagne_client.list_agents_campagne(campagne_id)
        return _enrichir_agents(response.agents)

    @strawberry.field
    def repartition_par_zone(self, info: strawberry.types.Info, campagne_id: str) -> list[ZoneRepartition]:
        """Tableau « répartition par zone » : une ligne par zone affectée
        (zone → agent responsable + avancement). Mêmes accès que agents_campagne."""
        user = require_auth(info)
        require_role(info, "ADMIN", "AGENT", "SUPERVISEUR")
        _verifier_acces_campagne(user, campagne_id)
        agents = _enrichir_agents(campagne_client.list_agents_campagne(campagne_id).agents)
        lignes = [
            ZoneRepartition(
                quartier=z.quartier,
                camp=z.camp,
                agent_id=a.agent_id,
                agent_username=a.username,
                nb_abonnes=z.nb_abonnes,
                nb_releves=z.nb_releves,
                pct=z.pct,
            )
            for a in agents
            for z in a.zones
        ]
        lignes.sort(key=lambda ligne: (ligne.quartier, ligne.camp))
        return lignes

    @strawberry.field
    def zones_disponibles(self, info: strawberry.types.Info) -> list[ZoneDisponible]:
        """Zones existantes (issues des compteurs) proposées à l'affectation —
        ADMIN, SUPERVISEUR."""
        require_auth(info)
        require_role(info, "ADMIN", "SUPERVISEUR")
        response = abonne_client.list_zones()
        return [ZoneDisponible(quartier=z.quartier, camp=z.camp, nb_abonnes=z.nb_abonnes) for z in response.zones]

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
