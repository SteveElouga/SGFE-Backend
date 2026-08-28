"""Conversion entre objets Django et messages protobuf du Facturation Service."""

import sys
from pathlib import Path

from django.conf import settings

sys.path.insert(0, str(Path(settings.BASE_DIR) / "proto"))

import facturation_service_pb2 as pb

from .models import Facture, Tarif


def facture_to_proto(facture: Facture) -> pb.FactureResponse:
    """Convertit une Facture Django en message protobuf FactureResponse."""
    return pb.FactureResponse(
        facture_id=str(facture.id),
        numero_facture=facture.numero_facture,
        abonne_id=str(facture.abonne_id),
        campagne_id=str(facture.campagne_id),
        ancien_index=float(facture.ancien_index),
        nouveau_index=float(facture.nouveau_index),
        consommation=float(facture.consommation),
        prix_m3=float(facture.prix_m3),
        montant=float(facture.montant),
        statut=facture.statut,
        date_releve=facture.date_releve.isoformat(),
        date_limite_paiement=facture.date_limite_paiement.isoformat(),
        date_generation=facture.date_generation.isoformat(),
        pdf_path=facture.pdf_path or "",
        numero_mobile_money=facture.numero_mobile_money,
        nature=facture.nature,
        motif=facture.motif or "",
        motif_annulation=facture.motif_annulation or "",
        date_annulation=facture.date_annulation.isoformat() if facture.date_annulation else "",
        annulee_par=facture.annulee_par or "",
        remplacee_par_id=facture.remplacee_par_id or "",
        remplace_id=facture.remplace_id or "",
    )


def tarif_to_proto(tarif: Tarif) -> pb.TarifResponse:
    """Convertit un Tarif Django en message protobuf TarifResponse."""
    return pb.TarifResponse(
        tarif_id=str(tarif.id),
        prix_m3=float(tarif.prix_m3),
        date_effet=tarif.date_effet.isoformat(),
        is_active=tarif.is_active,
    )
