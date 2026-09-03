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
