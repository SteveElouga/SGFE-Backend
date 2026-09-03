"""Structures de données légères échangées entre services.py/serializers.py et
les messages protobuf (voir grpc_server.py, qui les déballe en **kwargs).

Ces dicts ne correspondent à aucun modèle Django — un TypedDict leur donne une
forme précise sans recourir à `Any` ni à `dict` non paramétré.
"""

from typing import TypedDict


class StatsCampagneDict(TypedDict):
    campagne_id: str
    nom_campagne: str
    total_abonnes: int
    nb_releves: int
    nb_en_attente: int
    pourcentage_progression: float
    consommation_totale: float


class StatsFacturationDict(TypedDict):
    campagne_id: str
    total_factures: int
    montant_total_facture: float
    nb_factures_envoyees: int
    nb_factures_payees: int
    nb_impayes: int


class StatsPaiementsDict(TypedDict):
    campagne_id: str
    montant_encaisse: float
    montant_impaye: float
    nb_impayes: int
    taux_recouvrement: float


class FactureDict(TypedDict):
    facture_id: str
    statut: str
    montant: float


class PaiementDict(TypedDict):
    montant: float
    annule: bool
