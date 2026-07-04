"""Sérialisation entre les modèles Django et les messages protobuf du Paiement Service."""

import sys
from pathlib import Path

from django.conf import settings

sys.path.insert(0, str(Path(settings.BASE_DIR) / "proto"))

import paiement_service_pb2 as pb

from paiements.models import Paiement, SoldeFacture, SuiviImpaye


def paiement_to_proto(p: Paiement) -> pb.PaiementResponse:
    """Convertit un objet Paiement en message protobuf PaiementResponse."""
    return pb.PaiementResponse(
        paiement_id=str(p.id),
        facture_id=str(p.facture_id),
        montant=float(p.montant),
        date_paiement=p.date_paiement.isoformat() if p.date_paiement else "",
        mode_paiement=p.mode_paiement,
        reference_transaction=p.reference_transaction or "",
        created_at=p.created_at.isoformat() if p.created_at else "",
        enregistre_par=p.enregistre_par or "",
    )


def solde_to_proto(s: SoldeFacture) -> pb.SoldeResponse:
    """Convertit un objet SoldeFacture en message protobuf SoldeResponse."""
    return pb.SoldeResponse(
        facture_id=str(s.facture_id),
        montant_total=float(s.montant_total),
        montant_paye=float(s.montant_paye),
        solde_restant=float(s.solde_restant),
        statut=s.statut,
    )


def suivi_to_proto(s: SuiviImpaye) -> pb.SuiviImpayeResponse:
    """Convertit un objet SuiviImpaye en message protobuf SuiviImpayeResponse."""
    return pb.SuiviImpayeResponse(
        suivi_id=str(s.id),
        facture_id=str(s.facture_id),
        abonne_id=str(s.abonne_id),
        date_depassement=s.date_depassement.isoformat() if s.date_depassement else "",
        etape_actuelle=s.etape_actuelle,
        resolu_le=s.resolu_le.isoformat() if s.resolu_le else "",
    )
