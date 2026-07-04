"""Génération du PDF de facture d'eau (gabarit HTML → PDF via WeasyPrint).

Le rendu est fait à partir du gabarit Django `facture_pdf.html` (design
"AquaBill" fourni par l'équipe frontend, `factures/templates/facture_pdf.html`)
converti en PDF par WeasyPrint. `_build_context`/`build_historique` sont du
Python pur (aucune dépendance native) et donc testables partout ; seule
`generer_pdf` importe WeasyPrint, de façon **paresseuse**, pour que l'absence
des bibliothèques natives (pango/cairo) en environnement de test ne casse pas
l'import du module — l'échec de rendu est alors dégradé gracieusement par
l'appelant (voir services.py).
"""

import datetime
import io
import os
from dataclasses import dataclass
from decimal import Decimal
from pathlib import Path

from django.template.loader import render_to_string

_MOIS_FR = [
    "Janvier",
    "Février",
    "Mars",
    "Avril",
    "Mai",
    "Juin",
    "Juillet",
    "Août",
    "Septembre",
    "Octobre",
    "Novembre",
    "Décembre",
]

_MOIS_FR_ABBR = ["Jan", "Fév", "Mar", "Avr", "Mai", "Juin", "Juil", "Aoû", "Sep", "Oct", "Nov", "Déc"]

# Hauteur maximale (px) d'une barre de l'histogramme de consommation, alignée
# sur le gabarit facture_pdf.html (.hist__bars { height: 74px }, marge incluse).
_HIST_HAUTEUR_MAX_PX = 44
_HIST_HAUTEUR_MIN_PX = 4

# Version du gabarit/générateur de PDF. À **incrémenter** à chaque modification
# visible du rendu (`facture_pdf.html` ou construction du contexte) : une facture
# dont le PDF stocké porte une version différente est considérée obsolète et
# régénérée automatiquement (voir `services.py::get_pdf_bytes`). Sans ce
# marqueur, un changement de gabarit laisserait indéfiniment en cache les PDF
# déjà produits — d'où des abonnés recevant l'ancien rendu.
#   0 = PDF antérieurs au versioning (ReportLab / non marqués) → toujours obsolètes
#   1 = gabarit « AquaBill » (Django + WeasyPrint)
PDF_TEMPLATE_VERSION = 1


@dataclass
class InfosSociete:
    """Informations de l'entreprise qui apparaissent sur les factures."""

    nom: str = "Société de Distribution d'Eau"
    adresse: str = ""
    telephone: str = ""


@dataclass
class DonneesFacture:
    """Données nécessaires à la génération du PDF d'une facture.

    Les champs d'identité de l'abonné et de campagne sont optionnels : si
    Abonné Service ou Campagne Service est inaccessible au moment de la
    génération, le PDF est tout de même produit (repli sur l'identifiant
    technique ou une valeur vide), la facture ne doit jamais échouer.
    """

    numero_facture: str
    abonne_id: str
    campagne_id: str
    ancien_index: Decimal
    nouveau_index: Decimal
    consommation: Decimal
    prix_m3: Decimal
    montant: Decimal
    statut: str
    date_releve: str
    date_limite_paiement: str
    date_generation: str
    numero_mobile_money: str = ""
    # Identité de l'abonné (source : Abonné Service, facultative)
    numero_abonne: str = ""
    abonne_nom: str = ""
    abonne_prenom: str = ""
    abonne_whatsapp: str = ""
    abonne_adresse: str = ""
    numero_compteur: str = ""
    quartier: str = ""
    camp: str = ""
    # Contexte du relevé (facultatif). Le nom de la campagne est récupéré
    # auprès de Campagne Service ; l'agent qui a effectué le relevé et l'heure
    # exacte ne sont en revanche pas encore tracés par ce service — voir
    # ANO-027 (dette identifiée) dans docs/ETAT_DU_SYSTEME.md.
    campagne_nom: str = ""
    agent_username: str = ""
    heure_releve: str = ""
    # Lien de l'espace abonné (facultatif) : un token d'accès n'existe pas
    # forcément encore au moment de la génération initiale du PDF (il est créé
    # par Notification Service, uniquement si un envoi WhatsApp a lieu) — le
    # bloc correspondant est simplement masqué dans le gabarit si vide.
    espace_url: str = ""
    espace_date_expiration: str = ""


@dataclass
class MoisConsommation:
    """Un point de l'histogramme de consommation (6 derniers mois).

    `marge_haut_px` (= hauteur max - hauteur_px) est appliqué en `margin-top`
    sur la barre plutôt que de compter sur `align-items: flex-end` pour
    aligner les barres en bas du graphique : le support de l'alignement
    flexbox imbriqué de WeasyPrint s'est révélé peu fiable en pratique
    (barres qui ne respectent pas leur hauteur individuelle) — un espaceur
    explicite donne un résultat pixel-parfait quel que soit le moteur de rendu.
    """

    label: str
    conso: str
    hauteur_px: int
    marge_haut_px: int
    is_actuel: bool = False


def _fcfa(value: Decimal | float) -> str:
    """Formate un montant en FCFA (séparateur de milliers = espace)."""
    d = Decimal(str(value))
    if d == d.to_integral_value():
        entier = f"{int(d):,}".replace(",", " ")
        return f"{entier} FCFA"
    decimal = f"{d:,.2f}".replace(",", " ").replace(".", ",")
    return f"{decimal} FCFA"


def _num(value: Decimal | float) -> str:
    """Formate un index/consommation : entier groupé si entier, sinon décimales utiles."""
    d = Decimal(str(value))
    if d == d.to_integral_value():
        return f"{int(d):,}".replace(",", " ")
    return f"{d.normalize():f}"


def _date_fr(iso_date: str) -> str:
    """Convertit une date ISO 'YYYY-MM-DD' en 'JJ/MM/AAAA' (repli : chaîne brute)."""
    try:
        d = datetime.date.fromisoformat(iso_date)
        return d.strftime("%d/%m/%Y")
    except (ValueError, TypeError):
        return iso_date


def _periode_fr(iso_date: str) -> str:
    """Retourne le mois/année en français à partir d'une date ISO (ex. 'Juin 2026')."""
    try:
        d = datetime.date.fromisoformat(iso_date)
        return f"{_MOIS_FR[d.month - 1]} {d.year}"
    except (ValueError, TypeError, IndexError):
        return iso_date


def _modalite_delai(date_releve_iso: str, date_limite_iso: str) -> str:
    """Phrase de délai de règlement, dérivée de l'écart relevé → date limite.

    Le nombre de jours n'est jamais codé en dur : il reflète le
    `delai_paiement_jours` réellement appliqué à cette facture
    (`date_limite = date_releve + delai`), donc reste exact même pour une
    facture historique générée sous un délai différent. Repli neutre si les
    dates sont illisibles.
    """
    try:
        releve = datetime.date.fromisoformat(date_releve_iso)
        limite = datetime.date.fromisoformat(date_limite_iso)
    except (ValueError, TypeError):
        return "dans les meilleurs délais"
    jours = (limite - releve).days
    if jours <= 0:
        return "dans les meilleurs délais"
    return f"sous {jours} jour{'s' if jours > 1 else ''}"


def build_historique(entries: list[tuple[str, Decimal | float, bool]]) -> list[MoisConsommation]:
    """Construit les points de l'histogramme à partir de triplets (date_releve ISO, consommation, is_actuel).

    `entries` doit déjà être trié chronologiquement (le plus ancien en
    premier). La hauteur des barres est proportionnelle à la consommation
    maximale de la série, bornée à [4, 44] px.
    """
    if not entries:
        return []
    valeurs = [float(conso) for _, conso, _ in entries]
    max_conso = max(valeurs)

    points = []
    for (date_iso, conso, is_actuel), valeur in zip(entries, valeurs):
        if max_conso > 0:
            hauteur = max(
                _HIST_HAUTEUR_MIN_PX, min(_HIST_HAUTEUR_MAX_PX, round(valeur / max_conso * _HIST_HAUTEUR_MAX_PX))
            )
        else:
            hauteur = _HIST_HAUTEUR_MIN_PX
        try:
            mois_idx = datetime.date.fromisoformat(date_iso).month - 1
            label = _MOIS_FR_ABBR[mois_idx]
        except (ValueError, TypeError, IndexError):
            label = ""
        points.append(
            MoisConsommation(
                label=label,
                conso=_num(conso),
                hauteur_px=hauteur,
                marge_haut_px=_HIST_HAUTEUR_MAX_PX - hauteur,
                is_actuel=is_actuel,
            )
        )
    return points


def _nom_abonne(facture: DonneesFacture) -> tuple[str, str]:
    """Retourne (nom, prenom) affichés — repli sur l'identifiant technique si les deux sont vides."""
    if facture.abonne_nom or facture.abonne_prenom:
        return facture.abonne_nom, facture.abonne_prenom
    return f"Abonné {facture.abonne_id[:8]}", ""


def _build_context(
    facture: DonneesFacture,
    societe: InfosSociete,
    historique: list[MoisConsommation] | None = None,
) -> dict:
    """Construit le contexte de rendu attendu par le gabarit `facture_pdf.html`.

    Toutes les valeurs numériques sont pré-formatées en chaînes (voir contrat
    documenté en tête du gabarit) pour ne dépendre d'aucune locale WeasyPrint.
    """
    nom, prenom = _nom_abonne(facture)
    montant_fmt = _fcfa(facture.montant)

    return {
        "societe": {
            "nom": societe.nom or "Société de Distribution d'Eau",
            "adresse_ligne1": societe.adresse,
            "telephone": societe.telephone,
        },
        "facture": {
            "numero": facture.numero_facture,
            "date_releve": _date_fr(facture.date_releve),
            "date_limite_paiement": _date_fr(facture.date_limite_paiement),
            "designation": f"Consommation d'eau — {_periode_fr(facture.date_releve)}",
            "ancien_index": _num(facture.ancien_index),
            "nouveau_index": _num(facture.nouveau_index),
            "consommation": _num(facture.consommation),
            "prix_unitaire": _fcfa(facture.prix_m3),
            "montant": montant_fmt,
            # Aucun frais additionnel n'est modélisé par les règles métier
            # actuelles (CLAUDE.md : montant = consommation * prix_m3) — le
            # sous-total et le total sont donc identiques au montant tant
            # qu'une telle règle n'est pas introduite.
            "sous_total": montant_fmt,
            "frais_supplementaires": _fcfa(Decimal("0")),
            "total": montant_fmt,
            "date_generation": facture.date_generation,
            # Délai de règlement dérivé des dates de la facture (jamais codé
            # en dur) — reflète le delai_paiement_jours réellement appliqué.
            "modalite_delai": _modalite_delai(facture.date_releve, facture.date_limite_paiement),
        },
        "abonne": {
            "civilite": "",
            "nom": nom,
            "prenom": prenom,
            "numero": facture.numero_abonne or f"#{facture.abonne_id[:8]}",
            "quartier": f"Quartier {facture.quartier}" if facture.quartier else "",
            "camp": facture.camp,
            "whatsapp": facture.abonne_whatsapp,
        },
        "compteur": {"numero": facture.numero_compteur or "—"},
        "releve": {
            "campagne_nom": facture.campagne_nom or _periode_fr(facture.date_releve),
            "agent_username": facture.agent_username,
            "date": _date_fr(facture.date_releve),
            "heure": facture.heure_releve,
        },
        "historique": historique or [],
        "espace": {
            "url": facture.espace_url,
            # Notification renvoie une date ISO ; _date_fr laisse passer une
            # valeur déjà formatée (ou vide) sans la casser.
            "date_expiration": _date_fr(facture.espace_date_expiration),
        },
    }


def generer_pdf(
    facture: DonneesFacture,
    societe: InfosSociete,
    output_dir: str,
    historique: list[MoisConsommation] | None = None,
) -> str:
    """Génère le PDF d'une facture et retourne le chemin du fichier.

    WeasyPrint est importé paresseusement : en environnement sans les
    bibliothèques natives requises, l'exception remonte à l'appelant qui la
    traite en dégradation gracieuse (le PDF est régénérable à la demande).
    """
    import weasyprint  # import paresseux — voir docstring du module

    Path(output_dir).mkdir(parents=True, exist_ok=True)
    filename = f"{facture.numero_facture}.pdf"
    filepath = os.path.join(output_dir, filename)

    context = _build_context(facture, societe, historique)
    html_str = render_to_string("facture_pdf.html", context)
    buffer = io.BytesIO()
    # base_url : résout les URLs relatives du gabarit (polices Montserrat dans
    # templates/fonts/). Trailing slash indispensable pour que urljoin conserve
    # le dossier templates/ comme base.
    base_url = f"{Path(__file__).resolve().parent / 'templates'}/"
    weasyprint.HTML(string=html_str, base_url=base_url).write_pdf(buffer)

    with open(filepath, "wb") as f:
        f.write(buffer.getvalue())

    return filepath


def lire_pdf(filepath: str) -> bytes:
    """Lit le PDF depuis le système de fichiers et retourne ses octets."""
    with open(filepath, "rb") as f:
        return f.read()
