"""Génération du PDF « Reçu de paiement » (format A5, WeasyPrint).

Réutilise l'infrastructure PDF de la facture (WeasyPrint + polices Montserrat
embarquées dans templates/). `build_recu_context` et `_montant_en_lettres` sont
du Python pur (aucune dépendance native, testables partout) ; seul
`generer_recu_pdf_bytes` importe WeasyPrint, de façon paresseuse, pour que
l'absence des bibliothèques natives (pango/cairo) en environnement de test ne
casse pas l'import du module — l'échec de rendu est dégradé gracieusement par
l'appelant (voir services.py).

Design : maquette AquaBill « Reçu de Paiement » fournie par l'équipe frontend
(en-tête société + badge REÇU, cartes abonné/enregistrement, bloc montant reçu
avec montant en toutes lettres, table « situation de la facture », statut,
signatures, note dynamique, pied de page répété).
"""

import io
from dataclasses import dataclass
from decimal import Decimal
from pathlib import Path

from django.template.loader import render_to_string

from .pdf_generator import InfosSociete, _date_fr, _fcfa, _num, _periode_fr

# Version du gabarit/générateur du reçu. À incrémenter à chaque modification
# visible du rendu (`recu_pdf.html` ou construction du contexte).
RECU_TEMPLATE_VERSION = 1

# Libellés lisibles des modes de paiement (repli : mode brut « joli »).
_MODE_LABELS: dict[str, str] = {
    "ESPECES": "Espèces",
    "MOBILE_MONEY": "Mobile Money",
    "OM": "Orange Money",
    "ORANGE_MONEY": "Orange Money",
    "MOMO": "MTN MoMo",
    "MTN_MOMO": "MTN MoMo",
    "VIREMENT": "Virement bancaire",
    "CHEQUE": "Chèque",
    "CARTE": "Carte bancaire",
}

# Charte des statuts de solde (couleur texte / fond / bordure du badge).
_STATUT_STYLES: dict[str, dict[str, str]] = {
    "PAYEE": {"label": "Facture soldée", "texte": "#15803d", "fond": "#f0fdf4", "bordure": "#bbf7d0"},
    "PARTIELLE": {"label": "Facture partielle", "texte": "#1d4ed8", "fond": "#eff6ff", "bordure": "#bfdbfe"},
    "IMPAYEE": {"label": "Facture impayée", "texte": "#b91c1c", "fond": "#fef2f2", "bordure": "#fecaca"},
}

# ── Conversion d'un entier en toutes lettres (français) ──────────────────────
_UNITES = [
    "zéro",
    "un",
    "deux",
    "trois",
    "quatre",
    "cinq",
    "six",
    "sept",
    "huit",
    "neuf",
    "dix",
    "onze",
    "douze",
    "treize",
    "quatorze",
    "quinze",
    "seize",
    "dix-sept",
    "dix-huit",
    "dix-neuf",
]
_DIZAINES = {20: "vingt", 30: "trente", 40: "quarante", 50: "cinquante", 60: "soixante"}


def _sous_cent(n: int) -> str:
    """Nombre 0..99 en toutes lettres (gère 70/80/90 et les « et un »)."""
    if n < 20:
        return _UNITES[n]
    dizaine = (n // 10) * 10
    unite = n % 10
    if dizaine in (70, 90):
        # 70-79 = soixante + (dix..dix-neuf) ; 90-99 = quatre-vingt + (dix..dix-neuf)
        base = "soixante" if dizaine == 70 else "quatre-vingt"
        if dizaine == 70 and unite == 1:
            return "soixante et onze"
        return f"{base}-{_UNITES[10 + unite]}"
    if dizaine == 80:
        return "quatre-vingts" if unite == 0 else f"quatre-vingt-{_UNITES[unite]}"
    mot = _DIZAINES[dizaine]
    if unite == 0:
        return mot
    if unite == 1:
        return f"{mot} et un"
    return f"{mot}-{_UNITES[unite]}"


def _sous_mille(n: int) -> str:
    """Nombre 0..999 en toutes lettres."""
    if n < 100:
        return _sous_cent(n)
    centaines = n // 100
    reste = n % 100
    tete = "cent" if centaines == 1 else f"{_UNITES[centaines]} cent"
    if reste == 0:
        return tete + "s" if centaines > 1 else tete  # « deux cents », « cent »
    return f"{tete} {_sous_cent(reste)}"


def _groupe_avant_mille(n: int) -> str:
    """0..999 en lettres, en supprimant le « s » terminal de vingt/cent quand
    le groupe est suivi de « mille » (« deux cent mille », « quatre-vingt mille »)."""
    s = _sous_mille(n)
    if s.endswith("vingts") or s.endswith("cents"):
        s = s[:-1]
    return s


def _montant_en_lettres(montant: Decimal | float | int) -> str:
    """Montant entier (FCFA) en toutes lettres, ex. 10750 → « dix mille sept
    cent cinquante ». Les centimes sont ignorés (les montants FCFA sont entiers)."""
    n = int(Decimal(str(montant)))
    if n == 0:
        return "zéro"
    parts: list[str] = []
    millions = n // 1_000_000
    milliers = (n % 1_000_000) // 1000
    reste = n % 1000
    if millions:
        parts.append("un million" if millions == 1 else f"{_sous_mille(millions)} millions")
    if milliers:
        parts.append("mille" if milliers == 1 else f"{_groupe_avant_mille(milliers)} mille")
    if reste:
        parts.append(_sous_mille(reste))
    return " ".join(parts)


def _mode_label(mode: str) -> str:
    """Libellé lisible d'un mode de paiement."""
    if not mode:
        return "—"
    return _MODE_LABELS.get(mode.upper(), mode.replace("_", " ").title())


@dataclass
class DonneesRecu:
    """Données nécessaires à la génération du PDF d'un reçu de paiement.

    Les champs d'identité de l'abonné sont facultatifs : si Abonné Service est
    inaccessible au moment de la génération, le reçu est tout de même produit
    (repli sur l'identifiant technique), il ne doit jamais échouer.
    """

    numero_recu: str
    date_paiement: str  # ISO 'YYYY-MM-DD'
    montant: Decimal
    mode_paiement: str
    reference_transaction: str
    enregistre_par: str
    # Situation de la facture au moment du versement
    montant_total: Decimal
    total_verse: Decimal
    nb_versements: int
    solde_restant: Decimal
    statut: str
    # Facture rattachée
    numero_facture: str = ""
    facture_periode: str = ""  # ex. « Juin 2026 » (repli dérivé de date_paiement)
    # Identité de l'abonné (facultative)
    abonne_civilite: str = ""
    abonne_nom: str = ""
    abonne_prenom: str = ""
    numero_abonne: str = ""
    quartier: str = ""
    camp: str = ""
    abonne_id: str = ""
    # Contexte facultatif
    heure_paiement: str = ""  # ex. « 11h20 »
    delai_pause_jours: int = 5  # relances suspendues X jours après un acompte
    whatsapp_confirme: str = ""  # numéro si une confirmation WhatsApp a été envoyée


def _nom_complet(d: DonneesRecu) -> str:
    """Nom affiché de l'abonné — repli sur l'identifiant technique si vide."""
    parts = [p for p in (d.abonne_civilite, d.abonne_nom, d.abonne_prenom) if p]
    if parts:
        return " ".join(parts)
    return f"Abonné {d.abonne_id[:8]}" if d.abonne_id else "Abonné"


def _lieu(d: DonneesRecu) -> str:
    """Ligne « Quartier X · Camp Y » (segments présents uniquement)."""
    segments = []
    if d.quartier:
        segments.append(f"Quartier {d.quartier}")
    if d.camp:
        segments.append(f"Camp {d.camp}")
    return " · ".join(segments)


def _note(d: DonneesRecu, solde: Decimal) -> str:
    """Note de bas de reçu, adaptée au caractère partiel ou soldé du paiement."""
    if solde <= 0:
        phrase = f"Facture soldée — ce reçu confirme le règlement intégral de la facture {d.numero_facture}."
    else:
        phrase = (
            f"Un versement partiel suspend les relances pendant {d.delai_pause_jours} jours. "
            "Le solde restant demeure exigible — un reçu est émis pour chaque versement."
        )
    if d.whatsapp_confirme:
        phrase += f" Une confirmation WhatsApp a été envoyée au {d.whatsapp_confirme}."
    return phrase


def build_recu_context(recu: DonneesRecu, societe: InfosSociete) -> dict:
    """Construit le contexte de rendu attendu par le gabarit `recu_pdf.html`.

    Toutes les valeurs numériques sont pré-formatées en chaînes (séparateur de
    milliers = espace) pour ne dépendre d'aucune locale WeasyPrint, comme le
    contrat documenté en tête de `facture_pdf.html`.
    """
    solde = Decimal(str(recu.solde_restant))
    statut = (recu.statut or "").upper()
    style = _STATUT_STYLES.get(statut, _STATUT_STYLES["PARTIELLE"])
    periode = recu.facture_periode or _periode_fr(recu.date_paiement)
    date_fr = _date_fr(recu.date_paiement)
    date_heure = f"Le {date_fr}"
    if recu.heure_paiement:
        date_heure += f" à {recu.heure_paiement}"
    nb = recu.nb_versements or 1

    return {
        "societe": {
            "nom": societe.nom or "Société de Distribution d'Eau",
            "adresse_ligne1": societe.adresse,
            "telephone": societe.telephone,
        },
        "recu": {
            "numero": recu.numero_recu,
            "date": date_fr,
        },
        "abonne": {
            "nom_complet": _nom_complet(recu),
            "numero": recu.numero_abonne or (f"#{recu.abonne_id[:8]}" if recu.abonne_id else "—"),
            "lieu": _lieu(recu),
        },
        "enregistrement": {
            "date_heure": date_heure,
            "par": recu.enregistre_par or "—",
            "facture_ref": f"{recu.numero_facture} ({periode})" if recu.numero_facture else periode,
        },
        "versement": {
            "montant": _num(recu.montant),
            "montant_lettres": _montant_en_lettres(recu.montant).capitalize() + " francs CFA",
            "mode": _mode_label(recu.mode_paiement),
            "reference": recu.reference_transaction or "—",
            "a_reference": bool(recu.reference_transaction),
        },
        "situation": {
            "montant_total": _fcfa(recu.montant_total),
            "total_verse": _fcfa(recu.total_verse),
            "nb_versements_label": f"{nb} versement" + ("s" if nb > 1 else ""),
            "solde_restant": _fcfa(solde),
            "solde_positif": solde > 0,
            "statut_label": style["label"],
            "statut_texte": style["texte"],
            "statut_fond": style["fond"],
            "statut_bordure": style["bordure"],
        },
        "note": _note(recu, solde),
    }


def generer_recu_pdf_bytes(context: dict) -> bytes:  # pragma: no cover
    """Rend le gabarit `recu_pdf.html` en PDF (WeasyPrint, import paresseux).

    Non couvert par les tests unitaires : dépend des bibliothèques natives
    (pango/cairo) absentes de l'environnement de test — la construction du
    contexte (`build_recu_context`) est testée séparément, elle.
    """
    import weasyprint  # import paresseux — voir docstring du module

    html_str = render_to_string("recu_pdf.html", context)
    buffer = io.BytesIO()
    base_url = f"{Path(__file__).resolve().parent / 'templates'}/"
    weasyprint.HTML(string=html_str, base_url=base_url).write_pdf(buffer)
    return buffer.getvalue()
