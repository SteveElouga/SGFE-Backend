"""Structures de données légères échangées entre serializers.py et
grpc_server.py (messages protobuf).

Ces dicts ne correspondent à aucun modèle Django — un TypedDict leur donne une
forme précise sans recourir à `Any` ni à `dict` non paramétré.
"""

from typing import TypedDict


class CompteurResponseDict(TypedDict):
    compteur_id: str
    numero_compteur: int
    quartier: str
    camp: int
    index_initial: float
    date_pose: str
    statut: str
    position: str
    # `None` tant qu'aucune coordonnée n'a été posée (import initial pas
    # encore fait) — voir Compteur.latitude/longitude/date_maj_position.
    latitude: float | None
    longitude: float | None
    date_maj_position: str | None


class CoordonneeCompteurDict(TypedDict):
    """Une ligne brute (non validée) du CSV d'import de coordonnées —
    valeurs telles que reçues par `ImporterCoordonneesCompteurs`, avant toute
    validation (voir `CompteurService.importer_coordonnees`)."""

    numero_compteur: str
    latitude: str
    longitude: str


class ImportErreurDict(TypedDict):
    numero_compteur: str
    message: str


class ImportCoordonneesResultDict(TypedDict):
    nb_importees: int
    erreurs: list[ImportErreurDict]


class HistoriqueResponseDict(TypedDict):
    historique_id: str
    ancien_compteur: CompteurResponseDict
    nouveau_compteur: CompteurResponseDict
    index_fermeture: float
    date_remplacement: str
    created_at: str
    motif: str


class ZoneStatDict(TypedDict):
    quartier: str
    camp: int
    nb_abonnes: int


class AbonneResponseDict(TypedDict):
    abonne_id: str
    numero_abonne: str
    nom: str
    prenom: str
    telephone_whatsapp: str
    adresse: str
    statut: str
    created_at: str
    compteur: CompteurResponseDict | None
