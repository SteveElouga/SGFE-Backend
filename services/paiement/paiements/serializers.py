"""Sérialisation entre les modèles Django et les messages protobuf du Paiement Service."""

import sys
from pathlib import Path

from django.conf import settings

sys.path.insert(0, str(Path(settings.BASE_DIR) / "proto"))

import paiement_service_pb2 as pb

from django.db.models import Sum
from decimal import Decimal

from paiements.models import ModePaiement, MouvementAvoir, Paiement, SoldeFacture, SuiviImpaye


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
        abonne_id=str(p.abonne_id) if p.abonne_id else "",
        annule=p.annule,
        annule_le=p.annule_le.isoformat() if p.annule_le else "",
        annule_par=p.annule_par or "",
        motif_annulation=p.motif_annulation or "",
    )


def solde_to_proto(s: SoldeFacture) -> pb.SoldeResponse:
    """Convertit un objet SoldeFacture en message protobuf SoldeResponse.

    `avoir_impute` est recalculé depuis le journal des versements plutôt que
    stocké : c'est la somme des écritures de mode AVOIR sur cette facture, et
    elle se déduit sans risque de dérive. Sans elle, un abonné dont la facture a
    été réduite par un trop-perçu antérieur lit un « déjà réglé » dont il n'a
    aucun souvenir.
    """
    avoir_impute = Paiement.objects.filter(facture_id=s.facture_id, mode_paiement=ModePaiement.AVOIR).aggregate(
        t=Sum("montant")
    )["t"] or Decimal("0")
    return pb.SoldeResponse(
        avoir_impute=float(avoir_impute),
        facture_id=str(s.facture_id),
        montant_total=float(s.montant_total),
        montant_paye=float(s.montant_paye),
        solde_restant=float(s.solde_restant),
        statut=s.statut,
        abonne_id=str(s.abonne_id),
        date_limite_paiement=s.date_limite_paiement.isoformat() if s.date_limite_paiement else "",
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


def avoir_to_proto(abonne_id: str, montant: Decimal, mouvements: list[MouvementAvoir]) -> pb.AvoirResponse:
    """Construit le message AvoirResponse (solde d'avoir + journal des mouvements)."""
    return pb.AvoirResponse(
        abonne_id=str(abonne_id),
        montant=float(montant),
        mouvements=[
            pb.MouvementAvoir(
                montant=float(m.montant),
                type_mouvement=m.type_mouvement,
                motif=m.motif or "",
                facture_id=m.facture_id or "",
                cree_par=m.cree_par or "",
                created_at=m.created_at.isoformat() if m.created_at else "",
            )
            for m in mouvements
        ],
    )
