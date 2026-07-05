"""Génération du PDF « Bilan des impayés » (agrégat A4, WeasyPrint).

Réutilise l'infrastructure PDF de la facture (WeasyPrint + polices Montserrat
embarquées dans templates/). `build_bilan_context` est du Python pur (aucune
dépendance native, testable partout) ; seul `generer_bilan_pdf_bytes` importe
WeasyPrint, de façon paresseuse, pour que l'absence des bibliothèques natives
(pango/cairo) en environnement de test ne casse pas l'import du module.
"""

import io
from dataclasses import dataclass
from pathlib import Path

from django.template.loader import render_to_string

from .pdf_generator import InfosSociete, _fcfa, _num

# Version du gabarit/générateur du bilan. À incrémenter à chaque modification
# visible du rendu (`bilan_impayes.html` ou construction du contexte).
BILAN_TEMPLATE_VERSION = 1

# Libellés et couleurs par étape de relance (alignés sur la maquette AquaBill).
_ETAPE_LABELS: dict[int, str] = {
    1: "Étape 1 · Rappel doux",
    2: "Étape 2 · Rappel ferme",
    3: "Étape 3 · Avertissement",
    4: "Étape 4 · Suspendue",
}
_ETAPE_COULEURS: dict[int, str] = {
    1: "#f59e0b",
    2: "#c2410c",
    3: "#1a56db",
    4: "#7f1d1d",
}


@dataclass
class LigneImpaye:
    """Une créance impayée, déjà résolue (nom abonné, n° facture, étape)."""

    nom_complet: str
    numero_abonne: str
    numero_facture: str
    montant: float
    paye: float
    solde: float
    jours_retard: int
    etape: int  # 1..4
    en_pause: bool  # relances suspendues après un acompte reçu


def _retard_label(jours: int) -> str:
    return f"J+{jours}" if jours >= 0 else f"J{jours}"


def _badge_relance(ligne: LigneImpaye) -> str:
    if ligne.en_pause:
        return "Pause · acompte reçu"
    return _ETAPE_LABELS.get(ligne.etape, f"Étape {ligne.etape}")


def build_bilan_context(
    lignes: list[LigneImpaye],
    societe: InfosSociete,
    date_arrete,
    perimetre: str = "Ensemble des impayés",
) -> dict:
    """Construit le contexte de rendu du bilan (synthèse + répartition + lignes).

    Toutes les valeurs numériques sont pré-formatées en chaînes pour ne pas
    dépendre de la locale WeasyPrint (voir contrat de `facture_pdf.html`).
    """
    total_montant = sum(ligne.montant for ligne in lignes)
    total_paye = sum(ligne.paye for ligne in lignes)
    total_solde = sum(ligne.solde for ligne in lignes)
    nb_etape3_plus = sum(1 for ligne in lignes if ligne.etape >= 3)
    nb_suspendus = sum(1 for ligne in lignes if ligne.etape == 4)

    # Répartition du solde restant par étape de relance. `x` = décalage cumulé
    # (0..100) pour le tracé SVG de la barre côté gabarit.
    repartition: list[dict] = []
    offset = 0.0
    for etape in (1, 2, 3, 4):
        groupe = [ligne for ligne in lignes if ligne.etape == etape]
        if not groupe:
            continue
        solde_groupe = sum(ligne.solde for ligne in groupe)
        pct = round(solde_groupe / total_solde * 100, 1) if total_solde else 0.0
        repartition.append(
            {
                "label": _ETAPE_LABELS[etape],
                "couleur": _ETAPE_COULEURS[etape],
                "nb": len(groupe),
                "solde": _fcfa(solde_groupe),
                "pct": pct,
                "x": round(offset, 2),
            }
        )
        offset += pct

    lignes_ctx = [
        {
            "nom_complet": ligne.nom_complet,
            "numero_abonne": ligne.numero_abonne,
            "numero_facture": ligne.numero_facture,
            "montant": _num(ligne.montant),
            "paye": _num(ligne.paye),
            "solde": _num(ligne.solde),
            "retard": _retard_label(ligne.jours_retard),
            "badge": _badge_relance(ligne),
            "badge_couleur": _ETAPE_COULEURS.get(ligne.etape, "#64748b"),
            "critique": ligne.jours_retard >= 7 or ligne.etape >= 3,
        }
        for ligne in lignes
    ]

    return {
        "societe": societe,
        "perimetre": perimetre,
        "date_arrete": date_arrete.strftime("%d/%m/%Y"),
        "numero_bilan": f"BILAN-IMP-{date_arrete.strftime('%Y-%m-%d')}",
        "lignes": lignes_ctx,
        "nb_impayes": len(lignes),
        "total_montant": _num(total_montant),
        "total_paye": _num(total_paye),
        "total_solde": _num(total_solde),
        "total_solde_court": _fcfa(total_solde),
        "nb_etape3_plus": nb_etape3_plus,
        "nb_suspendus": nb_suspendus,
        "repartition": repartition,
    }


def generer_bilan_pdf_bytes(context: dict) -> bytes:
    """Rend le gabarit `bilan_impayes.html` en PDF (WeasyPrint, import paresseux)."""
    import weasyprint  # import paresseux — voir docstring du module

    html_str = render_to_string("bilan_impayes.html", context)
    buffer = io.BytesIO()
    base_url = f"{Path(__file__).resolve().parent / 'templates'}/"
    weasyprint.HTML(string=html_str, base_url=base_url).write_pdf(buffer)
    return buffer.getvalue()
