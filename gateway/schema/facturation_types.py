"""Types Strawberry du Facturation Service."""

from __future__ import annotations

import strawberry


@strawberry.type
class Facture:
    facture_id: str
    numero_facture: str
    abonne_id: str
    campagne_id: str
    ancien_index: float
    nouveau_index: float
    consommation: float
    prix_m3: float
    montant: float
    statut: str
    date_releve: str
    date_limite_paiement: str
    date_generation: str
    pdf_path: str
    numero_mobile_money: str


@strawberry.type
class Tarif:
    tarif_id: str
    prix_m3: float
    date_effet: str
    is_active: bool


def facture_from_grpc(r: object) -> Facture:
    """Convertit un FactureResponse protobuf en type Strawberry."""
    return Facture(
        facture_id=r.facture_id,
        numero_facture=r.numero_facture,
        abonne_id=r.abonne_id,
        campagne_id=r.campagne_id,
        ancien_index=r.ancien_index,
        nouveau_index=r.nouveau_index,
        consommation=r.consommation,
        prix_m3=r.prix_m3,
        montant=r.montant,
        statut=r.statut,
        date_releve=r.date_releve,
        date_limite_paiement=r.date_limite_paiement,
        date_generation=r.date_generation,
        pdf_path=r.pdf_path,
        numero_mobile_money=r.numero_mobile_money,
    )


def tarif_from_grpc(r: object) -> Tarif:
    """Convertit un TarifResponse protobuf en type Strawberry."""
    return Tarif(
        tarif_id=r.tarif_id,
        prix_m3=r.prix_m3,
        date_effet=r.date_effet,
        is_active=r.is_active,
    )
