from enum import Enum

import strawberry


@strawberry.enum
class StatutAbonne(Enum):
    ACTIF = "ACTIF"
    SUSPENDU = "SUSPENDU"
    RESILIE = "RESILIE"


@strawberry.enum
class StatutCompteur(Enum):
    ACTIF = "ACTIF"
    REMPLACE = "REMPLACE"
    DESACTIVE = "DESACTIVE"


@strawberry.type
class Compteur:
    id: strawberry.ID
    numero_compteur: int
    quartier: str
    camp: int
    index_initial: float
    date_pose: str
    statut: StatutCompteur
    position: str


@strawberry.type
class Abonne:
    id: strawberry.ID
    numero_abonne: str
    nom: str
    prenom: str
    telephone_whatsapp: str
    adresse: str | None
    statut: StatutAbonne
    compteur: Compteur | None
    created_at: str


@strawberry.input
class CreateAbonneInput:
    nom: str
    prenom: str
    telephone_whatsapp: str
    adresse: str | None = None
    numero_compteur: int
    quartier: str
    camp: int
    index_initial: float
    date_pose: str
    position: str | None = None


@strawberry.input
class UpdateAbonneInput:
    nom: str | None = None
    prenom: str | None = None
    telephone_whatsapp: str | None = None
    adresse: str | None = None


@strawberry.input
class UpdateCompteurInput:
    quartier: str | None = None
    camp: int | None = None
    index_initial: float | None = None
    date_pose: str | None = None
    position: str | None = None


@strawberry.input
class RemplacerCompteurInput:
    index_fermeture: float
    nouveau_numero_compteur: int
    nouveau_quartier: str
    nouveau_camp: int
    nouvel_index_initial: float
    date_remplacement: str
    motif: str = ""
    nouvelle_position: str = ""


@strawberry.type
class HistoriqueCompteur:
    id: strawberry.ID
    ancien_compteur: Compteur
    nouveau_compteur: Compteur
    index_fermeture: float
    date_remplacement: str
    created_at: str
    motif: str


def compteur_from_grpc(compteur_response) -> Compteur:
    return Compteur(
        id=strawberry.ID(compteur_response.compteur_id),
        numero_compteur=compteur_response.numero_compteur,
        quartier=compteur_response.quartier,
        camp=compteur_response.camp,
        index_initial=compteur_response.index_initial,
        date_pose=compteur_response.date_pose,
        statut=StatutCompteur(compteur_response.statut),
        position=compteur_response.position,
    )


def historique_from_grpc(h) -> HistoriqueCompteur:
    return HistoriqueCompteur(
        id=strawberry.ID(h.historique_id),
        ancien_compteur=compteur_from_grpc(h.ancien_compteur),
        nouveau_compteur=compteur_from_grpc(h.nouveau_compteur),
        index_fermeture=h.index_fermeture,
        date_remplacement=h.date_remplacement,
        created_at=h.created_at,
        motif=h.motif,
    )


def abonne_from_grpc(abonne_response) -> Abonne:
    has_compteur = abonne_response.HasField("compteur")
    return Abonne(
        id=strawberry.ID(abonne_response.abonne_id),
        numero_abonne=abonne_response.numero_abonne,
        nom=abonne_response.nom,
        prenom=abonne_response.prenom,
        telephone_whatsapp=abonne_response.telephone_whatsapp,
        adresse=abonne_response.adresse or None,
        statut=StatutAbonne(abonne_response.statut),
        compteur=compteur_from_grpc(abonne_response.compteur) if has_compteur else None,
        created_at=abonne_response.created_at,
    )
