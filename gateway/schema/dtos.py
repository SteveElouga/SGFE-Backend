"""Structures de données légères partagées entre modules du gateway (dicts non
Django/Strawberry). Un TypedDict leur donne une forme précise sans recourir à
`Any` ni à `dict` non paramétré.
"""

from typing import TypedDict


class FactureEspaceDict(TypedDict):
    facture_id: str
    numero: str
    date_releve: str
    montant: float
    statut: str
    date_limite_paiement: str
    solde_restant: float
    montant_paye: float
    ancien_index: float
    nouveau_index: float
    consommation: float
    prix_m3: float
    nature: str
    motif: str


class DonneesAbonneDict(TypedDict):
    abonne_id: str
    token_expiration: str
    avoir: float
    factures: list[FactureEspaceDict]
