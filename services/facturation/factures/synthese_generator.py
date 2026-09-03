"""Génération du PDF « Synthèse de campagne » (agrégat A4, WeasyPrint).

Document de l'écran 13 : reprend les statistiques des trois domaines (campagne,
facturation, paiements) fournies par le Reporting Service et les met en page en
cartes chiffrées. `build_synthese_context` est du Python pur (aucune dépendance
native, testable partout) ; seul `generer_synthese_pdf_bytes` importe WeasyPrint
de façon paresseuse (comme le bilan des impayés).
"""

import datetime
import io
from pathlib import Path
from typing import Any

from django.template.loader import render_to_string

from .pdf_generator import InfosSociete, _fcfa, _num

# Version du gabarit/générateur. À incrémenter à chaque modification visible du
# rendu (`synthese_campagne.html` ou construction du contexte).
SYNTHESE_TEMPLATE_VERSION = 1


def _pct(value: float | int) -> str:
    """Formate un pourcentage (une décimale, virgule française)."""
    return f"{float(value):.1f}".replace(".", ",") + " %"


def build_synthese_context(
    stats: dict[str, Any],
    societe: InfosSociete,
    campagne_id: str,
    date_edition: datetime.date,
) -> dict[str, Any]:
    """Construit le contexte de rendu de la synthèse (3 blocs chiffrés).

    `stats` est le dict renvoyé par ReportingServiceClient.get_stats_completes :
    `{campagne, facturation, paiements}`, chaque bloc pouvant être None (domaine
    sans données → affiché à zéro). Toutes les valeurs numériques sont
    pré-formatées en chaînes pour ne pas dépendre de la locale WeasyPrint.
    """
    campagne = stats.get("campagne") or {}
    facturation = stats.get("facturation") or {}
    paiements = stats.get("paiements") or {}

    nom_campagne = campagne.get("nom_campagne") or campagne_id

    return {
        "societe": societe,
        "campagne_id": campagne_id,
        "nom_campagne": nom_campagne,
        "date_edition": date_edition.strftime("%d/%m/%Y"),
        "numero_synthese": f"SYNTH-{date_edition.strftime('%Y-%m-%d')}",
        # Bloc campagne (relevés)
        "campagne": {
            "total_abonnes": _num(campagne.get("total_abonnes", 0)),
            "nb_releves": _num(campagne.get("nb_releves", 0)),
            "nb_en_attente": _num(campagne.get("nb_en_attente", 0)),
            "pourcentage_progression": _pct(campagne.get("pourcentage_progression", 0)),
            "consommation_totale": _num(campagne.get("consommation_totale", 0)) + " m³",
        },
        # Bloc facturation
        "facturation": {
            "total_factures": _num(facturation.get("total_factures", 0)),
            "montant_total_facture": _fcfa(facturation.get("montant_total_facture", 0)),
            "nb_factures_envoyees": _num(facturation.get("nb_factures_envoyees", 0)),
            "nb_factures_payees": _num(facturation.get("nb_factures_payees", 0)),
            "nb_impayes": _num(facturation.get("nb_impayes", 0)),
        },
        # Bloc paiements
        "paiements": {
            "montant_encaisse": _fcfa(paiements.get("montant_encaisse", 0)),
            "montant_impaye": _fcfa(paiements.get("montant_impaye", 0)),
            "nb_impayes": _num(paiements.get("nb_impayes", 0)),
            "taux_recouvrement": _pct(paiements.get("taux_recouvrement", 0)),
        },
    }


def generer_synthese_pdf_bytes(context: dict[str, Any]) -> bytes:
    """Rend le gabarit `synthese_campagne.html` en PDF (WeasyPrint, import paresseux)."""
    import weasyprint  # import paresseux — voir docstring du module

    html_str = render_to_string("synthese_campagne.html", context)
    buffer = io.BytesIO()
    base_url = f"{Path(__file__).resolve().parent / 'templates'}/"
    weasyprint.HTML(string=html_str, base_url=base_url).write_pdf(buffer)
    return buffer.getvalue()
