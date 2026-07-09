"""Types GraphQL du Campagne Service (campagnes + relevés)."""

from typing import Optional

import strawberry

from proto import campagne_service_pb2 as campagne_pb


@strawberry.type
class Campagne:
    campagne_id: str
    nom: str
    periode_mois: int
    periode_annee: int
    statut: str
    date_planifiee: str
    date_creation: str
    date_cloture: str
    numero_mobile_money: str
    generer_factures_auto: bool
    envoyer_whatsapp_auto: bool


@strawberry.type
class Auteur:
    """Auteur d'une action sur un relevé (snapshot au moment de l'action)."""

    id: str
    username: str
    role: str


@strawberry.type
class ReleveAudit:
    """Une entrée du journal d'audit d'un relevé (saisie initiale ou correction)."""

    action: str  # SAISIE | CORRECTION
    auteur: Auteur
    ancien_index: float  # index avant l'action (référence)
    nouvel_index: float  # index posé par l'action
    horodatage: str  # ISO 8601


@strawberry.type
class Releve:
    releve_id: str
    abonne_id: str
    ancien_index: float
    nouveau_index: float
    consommation: float
    date_releve: str
    observation: str
    statut: str
    # ID de l'agent/admin ayant saisi le relevé (P3 — écran « tournée agent »).
    agent_id: str
    # Auteur et horodatage de la saisie initiale (P1), dérivés du journal d'audit.
    saisi_par: Optional[Auteur]
    saisi_le: str
    # Journal complet SAISIE/CORRECTION, du plus ancien au plus récent (P2).
    audit: list[ReleveAudit]
    # Zone du compteur (snapshot posé à la création du relevé).
    quartier: str = ""
    camp: int = 0
    # Identité de l'abonné — enrichie par la Gateway via Abonné Service pour que
    # l'écran affiche des noms/adresses et non des UUID. Reste vide si Abonné
    # Service est indisponible (enrichissement best-effort, non bloquant).
    abonne_nom: str = ""
    abonne_prenom: str = ""
    numero_abonne: str = ""
    abonne_adresse: str = ""
    numero_compteur: int = 0


@strawberry.type
class Progression:
    campagne_id: str
    total_abonnes: int
    nb_releves: int
    nb_en_attente: int
    pourcentage: float


@strawberry.type
class AjouterAbonnesResult:
    """Résultat de l'ajout d'abonnés à une campagne (pré-création des relevés)."""

    nb_ajoutes: int  # relevés A_RELEVER créés
    nb_ignores: int  # abonnés déjà inscrits ou non ACTIF


@strawberry.type
class ResumeCloture:
    """Aperçu prêt à afficher avant la clôture d'une campagne (modal de confirmation)."""

    campagne_id: str
    total_abonnes: int
    nb_releves: int  # statut RELEVE
    nb_estimes: int  # statut ESTIME
    nb_non_releves: int  # statut NON_RELEVE
    nb_restants: int  # pas encore traités (A_RELEVER)
    nb_factures_a_generer: int  # relevés + estimés (les seuls facturés)


@strawberry.type
class DernierIndex:
    abonne_id: str
    dernier_index: float
    est_index_initial: bool


@strawberry.type
class ZoneStat:
    """Une zone (quartier + camp) affectée à un agent, avec son avancement."""

    quartier: str
    camp: int
    nb_abonnes: int  # abonnés actifs de la zone (dénominateur, via Abonné Service)
    nb_releves: int  # relevés RELEVE de la zone
    pct: float  # avancement de la zone (0-100)


@strawberry.type
class AgentAffecte:
    """Un agent affecté à une campagne (globalement et/ou par zone) + son état."""

    agent_id: str
    username: str
    role: str
    statut: str  # EN_TOURNEE | ACTIF | EN_RETARD | INACTIF (dérivé du dernier relevé)
    derniere_activite: str  # ISO 8601 du dernier relevé de l'agent, "" si aucun
    nb_releves: int  # total relevés RELEVE saisis par l'agent
    zones: list[ZoneStat]


@strawberry.type
class ZoneDisponible:
    """Zone existante (issue des compteurs) proposée à l'affectation."""

    quartier: str
    camp: int
    nb_abonnes: int


@strawberry.type
class ZoneRepartition:
    """Une ligne du tableau « répartition par zone » (zone + agent responsable)."""

    quartier: str
    camp: int
    agent_id: str
    agent_username: str
    nb_abonnes: int
    nb_releves: int
    pct: float


@strawberry.input
class ZoneInput:
    quartier: str
    camp: int


@strawberry.input
class CreateCampagneInput:
    nom: str
    periode_mois: int
    periode_annee: int
    date_planifiee: str = ""
    numero_mobile_money: str = ""
    generer_factures_auto: bool = True
    envoyer_whatsapp_auto: bool = True
    demarrer_maintenant: bool = False


@strawberry.input
class SaisirIndexInput:
    campagne_id: str
    abonne_id: str
    nouveau_index: float
    observation: str = ""


@strawberry.input
class CorrigerReleveInput:
    campagne_id: str
    abonne_id: str
    nouveau_index: float
    observation: str = ""


@strawberry.input
class MarquerNonReleveInput:
    campagne_id: str
    abonne_id: str
    statut: str = "NON_RELEVE"
    observation: str = ""


def campagne_from_grpc(r: campagne_pb.CampagneResponse) -> Campagne:
    return Campagne(
        campagne_id=r.campagne_id,
        nom=r.nom,
        periode_mois=r.periode_mois,
        periode_annee=r.periode_annee,
        statut=r.statut,
        date_planifiee=r.date_planifiee,
        date_creation=r.date_creation,
        date_cloture=r.date_cloture,
        numero_mobile_money=r.numero_mobile_money,
        generer_factures_auto=r.generer_factures_auto,
        envoyer_whatsapp_auto=r.envoyer_whatsapp_auto,
    )


def _audit_from_grpc(a: campagne_pb.ReleveAudit) -> ReleveAudit:
    return ReleveAudit(
        action=a.action,
        auteur=Auteur(id=a.auteur_id, username=a.auteur_username, role=a.auteur_role),
        ancien_index=a.ancien_index,
        nouvel_index=a.nouvel_index,
        horodatage=a.horodatage,
    )


def releve_from_grpc(r: campagne_pb.ReleveResponse) -> Releve:
    audit = [_audit_from_grpc(a) for a in r.audit]
    # saisiPar / saisiLe = auteur et horodatage de la saisie initiale (première
    # entrée SAISIE). Absents pour les relevés d'avant l'introduction de l'audit.
    saisie = next((a for a in audit if a.action == "SAISIE"), None)
    return Releve(
        releve_id=r.releve_id,
        abonne_id=r.abonne_id,
        ancien_index=r.ancien_index,
        nouveau_index=r.nouveau_index,
        consommation=r.consommation,
        date_releve=r.date_releve,
        observation=r.observation,
        statut=r.statut,
        agent_id=r.agent_id,
        saisi_par=saisie.auteur if saisie else None,
        saisi_le=saisie.horodatage if saisie else "",
        audit=audit,
        quartier=r.quartier,
        camp=r.camp,
    )
