"""Types GraphQL du Campagne Service (campagnes + relevés)."""

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


@strawberry.type
class Progression:
    campagne_id: str
    total_abonnes: int
    nb_releves: int
    nb_en_attente: int
    pourcentage: float


@strawberry.type
class DernierIndex:
    abonne_id: str
    dernier_index: float
    est_index_initial: bool


@strawberry.input
class CreateCampagneInput:
    nom: str
    periode_mois: int
    periode_annee: int
    date_planifiee: str = ""


@strawberry.input
class SaisirIndexInput:
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
    )


def releve_from_grpc(r: campagne_pb.ReleveResponse) -> Releve:
    return Releve(
        releve_id=r.releve_id,
        abonne_id=r.abonne_id,
        ancien_index=r.ancien_index,
        nouveau_index=r.nouveau_index,
        consommation=r.consommation,
        date_releve=r.date_releve,
        observation=r.observation,
        statut=r.statut,
    )
