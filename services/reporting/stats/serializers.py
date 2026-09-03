"""Conversion des modèles de stats en dicts pour les messages protobuf."""

from stats.dtos import StatsCampagneDict, StatsFacturationDict, StatsPaiementsDict
from stats.models import StatsCampagne, StatsFacturation, StatsPaiements


def stats_campagne_to_dict(s: StatsCampagne) -> StatsCampagneDict:
    return {
        "campagne_id": str(s.campagne_id),
        "nom_campagne": s.nom_campagne,
        "total_abonnes": s.total_abonnes,
        "nb_releves": s.nb_releves,
        "nb_en_attente": s.nb_en_attente,
        "pourcentage_progression": float(s.pourcentage_progression),
        "consommation_totale": float(s.consommation_totale),
    }


def stats_facturation_to_dict(s: StatsFacturation) -> StatsFacturationDict:
    return {
        "campagne_id": str(s.campagne_id),
        "total_factures": s.total_factures,
        "montant_total_facture": float(s.montant_total_facture),
        "nb_factures_envoyees": s.nb_factures_envoyees,
        "nb_factures_payees": s.nb_factures_payees,
        "nb_impayes": s.nb_factures_impayees,
    }


def stats_paiements_to_dict(s: StatsPaiements) -> StatsPaiementsDict:
    return {
        "campagne_id": str(s.campagne_id),
        "montant_encaisse": float(s.montant_encaisse),
        "montant_impaye": float(s.montant_impaye),
        "nb_impayes": s.nb_impayes,
        "taux_recouvrement": float(s.taux_recouvrement),
    }
