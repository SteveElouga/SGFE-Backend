"""Types Strawberry du Facturation Service."""

from __future__ import annotations

from typing import Any

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
    # Champs enrichis côté Gateway (jointure best-effort) pour les écrans qui
    # n'ont pas accès aux services Abonné/Campagne — typiquement le COMPTABLE,
    # à qui les queries `abonnes`/`campagnes` sont refusées : il obtient ainsi
    # le nom de l'abonné et le nom/période de la campagne sans appel séparé.
    abonne_nom: str = ""
    abonne_numero: str = ""
    campagne_nom: str = ""
    campagne_periode_mois: int = 0
    campagne_periode_annee: int = 0
    # CONSOMMATION (issue d'un relevé) | REGULARISATION (dette déclarée).
    # Sans elle, une régularisation s'affiche comme une facture d'eau à qui il
    # manquerait son index — un tiret là où l'on cherche des mètres cubes.
    nature: str = "CONSOMMATION"
    # Justification d'une régularisation — remplace le relevé absent.
    motif: str = ""
    # ── Annulation ────────────────────────────────────────────────────────────
    # Une facture annulée reste au journal : la supprimer laisserait un trou
    # dans la numérotation comptable, et le trou est précisément ce qui prouve
    # qu'on a effacé quelque chose.
    motif_annulation: str = ""
    date_annulation: str = ""
    annulee_par: str = ""
    # Les deux bouts d'une correction se citent : sans ce lien, le journal
    # montre une facture annulée et une autre née le même jour, sans rien qui
    # dise que la seconde répare la première.
    remplacee_par_id: str = ""
    remplace_id: str = ""


@strawberry.type
class Tarif:
    tarif_id: str
    prix_m3: float
    date_effet: str
    is_active: bool


def facture_from_grpc(r: Any) -> Facture:
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
        nature=getattr(r, "nature", "") or "CONSOMMATION",
        motif=getattr(r, "motif", "") or "",
        motif_annulation=getattr(r, "motif_annulation", "") or "",
        date_annulation=getattr(r, "date_annulation", "") or "",
        annulee_par=getattr(r, "annulee_par", "") or "",
        remplacee_par_id=getattr(r, "remplacee_par_id", "") or "",
        remplace_id=getattr(r, "remplace_id", "") or "",
    )


def tarif_from_grpc(r: Any) -> Tarif:
    """Convertit un TarifResponse protobuf en type Strawberry."""
    return Tarif(
        tarif_id=r.tarif_id,
        prix_m3=r.prix_m3,
        date_effet=r.date_effet,
        is_active=r.is_active,
    )


@strawberry.type
class RegenerationFacture:
    """Les deux bouts d'une correction, rendus ensemble.

    L'écran a besoin des deux : dire ce qui a été annulé, et ouvrir ce qui l'a
    remplacé. Ne renvoyer que la nouvelle obligerait à relire l'ancienne pour
    savoir ce qu'on vient de faire.
    """

    annulee: Facture
    nouvelle: Facture
