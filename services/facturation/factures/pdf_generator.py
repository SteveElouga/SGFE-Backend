"""Génération du PDF de facture d'eau (gabarit HTML → PDF via WeasyPrint).

Le rendu est fait à partir d'un gabarit HTML/CSS (`_build_html`) converti en PDF
par WeasyPrint. `_build_html` est du Python pur (aucune dépendance native) et donc
testable partout ; seule `generer_pdf` importe WeasyPrint, de façon **paresseuse**,
pour que l'absence des bibliothèques natives (pango/cairo) en environnement de test
ne casse pas l'import du module — l'échec de rendu est alors dégradé gracieusement
par l'appelant (voir services.py).
"""

import datetime
import html
import io
import os
from dataclasses import dataclass
from decimal import Decimal
from pathlib import Path

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


@dataclass
class InfosSociete:
    """Informations de l'entreprise qui apparaissent sur les factures."""

    nom: str = "Société de Distribution d'Eau"
    adresse: str = ""
    telephone: str = ""


@dataclass
class DonneesFacture:
    """Données nécessaires à la génération du PDF d'une facture.

    Les champs d'identité de l'abonné sont optionnels : si Abonné Service est
    inaccessible au moment de la génération, le PDF est tout de même produit
    (repli sur l'identifiant technique), la facture ne doit jamais échouer.
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


def _e(value: str) -> str:
    """Échappe une valeur dynamique pour l'insertion sûre dans le HTML."""
    return html.escape(value or "")


def _nom_complet(facture: DonneesFacture) -> str:
    """Nom affiché de l'abonné (repli : identifiant technique tronqué)."""
    nom = f"{facture.abonne_nom} {facture.abonne_prenom}".strip()
    if nom:
        return nom
    return f"Abonné {facture.abonne_id[:8]}"


def _localisation(facture: DonneesFacture) -> str:
    """Ligne quartier · camp de l'abonné (vide si aucune info)."""
    parts = []
    if facture.quartier:
        parts.append(f"Quartier {facture.quartier}")
    if facture.camp:
        parts.append(f"Camp {facture.camp}")
    return " · ".join(parts)


def _build_html(facture: DonneesFacture, societe: InfosSociete) -> str:
    """Construit le gabarit HTML complet de la facture (Python pur, sans WeasyPrint)."""
    nom_societe = _e(societe.nom or "Société de Distribution d'Eau")
    numero_abonne = _e(facture.numero_abonne or f"#{facture.abonne_id[:8]}")
    localisation = _e(_localisation(facture))
    adresse_abonne = _e(facture.abonne_adresse)
    compteur = _e(facture.numero_compteur or "—")
    periode = _e(_periode_fr(facture.date_releve))

    ligne_societe_adresse = f"<div class='muted'>{_e(societe.adresse)}</div>" if societe.adresse else ""
    ligne_societe_tel = f"<div class='muted'>{_e(societe.telephone)}</div>" if societe.telephone else ""
    ligne_whatsapp = (
        f"<div class='muted'>WhatsApp : {_e(facture.abonne_whatsapp)}</div>" if facture.abonne_whatsapp else ""
    )
    ligne_localisation = f"<div class='muted'>{localisation}</div>" if localisation else ""
    ligne_adresse_abonne = f"<div class='muted'>{adresse_abonne}</div>" if adresse_abonne else ""

    delai_jours = ""
    try:
        d_releve = datetime.date.fromisoformat(facture.date_releve)
        d_limite = datetime.date.fromisoformat(facture.date_limite_paiement)
        delai_jours = f"{(d_limite - d_releve).days} jours"
    except (ValueError, TypeError):
        pass
    regle = f"Règlement sous {delai_jours} : " if delai_jours else "Règlement : "

    ligne_mobile_money = (
        f"<div class='pay-line'>Paiement Mobile Money au <strong>{_e(facture.numero_mobile_money)}</strong></div>"
        if facture.numero_mobile_money
        else ""
    )

    return f"""<!DOCTYPE html>
<html lang="fr">
<head>
<meta charset="utf-8">
<style>
  @page {{ size: A4; margin: 12mm; }}
  * {{ box-sizing: border-box; }}
  body {{
    font-family: 'Liberation Sans', 'DejaVu Sans', Arial, sans-serif;
    color: #1E293B; font-size: 10.5px; margin: 0;
  }}
  .top-bar {{ height: 6px; border-radius: 6px 6px 0 0;
    background: linear-gradient(90deg, #0F1B3D 0%, #2563EB 55%, #16A34A 100%); }}
  .sheet {{ border: 1px solid #E2E8F0; border-top: none; border-radius: 0 0 10px 10px;
    padding: 22px 26px 16px 26px; }}
  .muted {{ color: #64748B; }}
  .row {{ display: table; width: 100%; table-layout: fixed; }}
  .cell {{ display: table-cell; vertical-align: top; }}
  .right {{ text-align: right; }}

  .brand {{ font-size: 17px; font-weight: bold; color: #0F1B3D; }}
  .drop {{ display: inline-block; width: 26px; height: 26px; vertical-align: middle; margin-right: 8px; }}
  .facture-title {{ font-size: 30px; font-weight: bold; color: #0F1B3D; letter-spacing: 1px; line-height: 1; }}
  .facture-num {{ font-size: 13px; font-weight: bold; color: #2563EB; margin-top: 2px; }}
  .date-line {{ margin-top: 6px; color: #64748B; }}
  .date-limite strong {{ color: #DC2626; }}

  .cards {{ display: table; width: 100%; table-layout: fixed; margin: 22px 0 6px 0; border-spacing: 12px 0; }}
  .card {{ display: table-cell; width: 50%; border: 1px solid #E2E8F0; border-radius: 8px; padding: 14px 16px; }}
  .card-label {{ font-size: 9px; letter-spacing: 1.5px; text-transform: uppercase; color: #94A3B8; font-weight: bold; }}
  .card-title {{ font-size: 14px; font-weight: bold; color: #0F1B3D; margin: 6px 0 4px 0; }}

  table.items {{ width: 100%; border-collapse: collapse; margin-top: 20px; }}
  table.items thead th {{ background: #0F1B3D; color: #fff; font-size: 9px; letter-spacing: 0.6px;
    text-transform: uppercase; padding: 12px 10px; text-align: center; }}
  table.items thead th:first-child {{ text-align: left; }}
  table.items tbody td {{ padding: 16px 10px; text-align: center; border-bottom: 1px solid #E2E8F0; }}
  table.items tbody td:first-child {{ text-align: left; font-weight: bold; color: #0F1B3D; }}
  .amount {{ font-weight: bold; color: #0F1B3D; }}

  .totals {{ display: table; width: 100%; margin-top: 14px; }}
  .totals-right {{ display: table-cell; }}
  .subtotal {{ text-align: right; color: #64748B; padding: 4px 6px; }}
  .subtotal .val {{ color: #0F1B3D; font-weight: bold; display: inline-block; min-width: 130px; }}
  .total-box {{ margin-top: 8px; background: #0F1B3D; color: #fff; border-radius: 8px;
    padding: 16px 22px; display: table; width: 100%; }}
  .total-label {{ display: table-cell; font-size: 11px; letter-spacing: 1px; text-transform: uppercase; }}
  .total-value {{ display: table-cell; text-align: right; font-size: 20px; font-weight: bold; }}

  .pay {{ margin-top: 22px; background: #F8FAFC; border: 1px solid #E2E8F0; border-radius: 8px; padding: 14px 16px; }}
  .pay-title {{ font-weight: bold; color: #0F1B3D; margin-bottom: 4px; }}
  .pay-line {{ color: #64748B; margin-top: 2px; }}

  .footer {{ margin-top: 20px; padding-top: 10px; border-top: 2px solid #0F1B3D;
    display: table; width: 100%; color: #94A3B8; font-size: 9px; }}
  .footer .cell.right {{ text-align: right; }}
</style>
</head>
<body>
  <div class="top-bar"></div>
  <div class="sheet">

    <div class="row">
      <div class="cell">
        <div class="brand">
          <svg class="drop" viewBox="0 0 24 24"><path fill="#0F1B3D"
            d="M12 2.5c4 5 7 8.4 7 12a7 7 0 1 1-14 0c0-3.6 3-7 7-12z"/></svg>{nom_societe}
        </div>
        {ligne_societe_adresse}
        {ligne_societe_tel}
      </div>
      <div class="cell right">
        <div class="facture-title">FACTURE</div>
        <div class="facture-num">{_e(facture.numero_facture)}</div>
        <div class="date-line">Date de relevé : {_e(_date_fr(facture.date_releve))}</div>
        <div class="date-line date-limite">Date limite de paiement :
          <strong>{_e(_date_fr(facture.date_limite_paiement))}</strong></div>
      </div>
    </div>

    <div class="cards">
      <div class="card">
        <div class="card-label">Facturé à</div>
        <div class="card-title">{_e(_nom_complet(facture))}</div>
        <div class="muted">Abonné N° {numero_abonne}</div>
        {ligne_localisation}
        {ligne_adresse_abonne}
        {ligne_whatsapp}
      </div>
      <div class="card">
        <div class="card-label">Compteur</div>
        <div class="card-title">N° {compteur}</div>
        <div class="muted">Période : {periode}</div>
      </div>
    </div>

    <table class="items">
      <thead>
        <tr>
          <th>Désignation</th><th>Ancien index</th><th>Nouvel index</th>
          <th>Conso (m³)</th><th>Prix unitaire</th><th>Montant</th>
        </tr>
      </thead>
      <tbody>
        <tr>
          <td>Consommation d'eau — {periode}</td>
          <td>{_num(facture.ancien_index)}</td>
          <td>{_num(facture.nouveau_index)}</td>
          <td>{_num(facture.consommation)}</td>
          <td>{_fcfa(facture.prix_m3)}</td>
          <td class="amount">{_fcfa(facture.montant)}</td>
        </tr>
      </tbody>
    </table>

    <div class="totals">
      <div class="totals-right">
        <div class="subtotal">Sous-total <span class="val">{_fcfa(facture.montant)}</span></div>
        <div class="total-box">
          <div class="total-label">Total à payer</div>
          <div class="total-value">{_fcfa(facture.montant)}</div>
        </div>
      </div>
    </div>

    <div class="pay">
      <div class="pay-title">Modalités de paiement</div>
      <div class="pay-line">{regle}Espèces · Mobile Money · Virement — auprès de notre service comptable.</div>
      {ligne_mobile_money}
    </div>

    <div class="footer row">
      <div class="cell">{nom_societe} — Système de facturation d'eau</div>
      <div class="cell right">Facture générée automatiquement le {_e(facture.date_generation)} — Page 1/1</div>
    </div>

  </div>
</body>
</html>"""


def generer_pdf(
    facture: DonneesFacture,
    societe: InfosSociete,
    output_dir: str,
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

    html_str = _build_html(facture, societe)
    buffer = io.BytesIO()
    weasyprint.HTML(string=html_str).write_pdf(buffer)

    with open(filepath, "wb") as f:
        f.write(buffer.getvalue())

    return filepath


def lire_pdf(filepath: str) -> bytes:
    """Lit le PDF depuis le système de fichiers et retourne ses octets."""
    with open(filepath, "rb") as f:
        return f.read()
