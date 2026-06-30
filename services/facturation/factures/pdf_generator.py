"""Génération de PDF pour les factures d'eau (ReportLab)."""

import io
import os
from dataclasses import dataclass
from decimal import Decimal
from pathlib import Path

from reportlab.lib import colors
from reportlab.lib.pagesizes import A4
from reportlab.lib.styles import getSampleStyleSheet
from reportlab.lib.units import cm
from reportlab.platypus import Paragraph, SimpleDocTemplate, Spacer, Table, TableStyle


@dataclass
class InfosSociete:
    """Informations de l'entreprise qui apparaissent sur les factures."""

    nom: str = "Société de Distribution d'Eau"
    adresse: str = ""
    telephone: str = ""


@dataclass
class DonneesFacture:
    """Données nécessaires à la génération du PDF d'une facture."""

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


def generer_pdf(
    facture: DonneesFacture,
    societe: InfosSociete,
    output_dir: str,
) -> str:
    """Génère le PDF d'une facture et retourne le chemin du fichier.

    Le fichier est nommé d'après le numéro de facture (ex. FACT-2025-07-0001.pdf).
    """
    Path(output_dir).mkdir(parents=True, exist_ok=True)
    filename = f"{facture.numero_facture}.pdf"
    filepath = os.path.join(output_dir, filename)

    buffer = io.BytesIO()
    doc = SimpleDocTemplate(
        buffer,
        pagesize=A4,
        topMargin=2 * cm,
        bottomMargin=2 * cm,
        leftMargin=2 * cm,
        rightMargin=2 * cm,
    )

    styles = getSampleStyleSheet()
    story = []

    # --- En-tête société ---
    story.append(Paragraph(f"<b>{societe.nom}</b>", styles["Title"]))
    if societe.adresse:
        story.append(Paragraph(societe.adresse, styles["Normal"]))
    if societe.telephone:
        story.append(Paragraph(f"Tél : {societe.telephone}", styles["Normal"]))
    story.append(Spacer(1, 0.5 * cm))

    # --- Titre facture ---
    story.append(
        Paragraph(f"<b>FACTURE N° {facture.numero_facture}</b>", styles["Heading1"])
    )
    story.append(Spacer(1, 0.3 * cm))

    # --- Infos générales ---
    infos = [
        ["Date de génération", facture.date_generation],
        ["Date du relevé", facture.date_releve],
        ["Date limite de paiement", facture.date_limite_paiement],
        ["Abonné (ID)", facture.abonne_id],
        ["Campagne (ID)", facture.campagne_id],
        ["Statut", facture.statut],
    ]
    table_infos = Table(infos, colWidths=[6 * cm, 10 * cm])
    table_infos.setStyle(
        TableStyle(
            [
                ("FONTNAME", (0, 0), (-1, -1), "Helvetica"),
                ("FONTNAME", (0, 0), (0, -1), "Helvetica-Bold"),
                ("FONTSIZE", (0, 0), (-1, -1), 10),
                ("BOTTOMPADDING", (0, 0), (-1, -1), 4),
                ("TOPPADDING", (0, 0), (-1, -1), 4),
            ]
        )
    )
    story.append(table_infos)
    story.append(Spacer(1, 0.5 * cm))

    # --- Détail de consommation ---
    story.append(Paragraph("<b>Détail de consommation</b>", styles["Heading2"]))
    detail = [
        ["Libellé", "Valeur"],
        ["Ancien index (m³)", f"{facture.ancien_index:.3f}"],
        ["Nouvel index (m³)", f"{facture.nouveau_index:.3f}"],
        ["Consommation (m³)", f"{facture.consommation:.3f}"],
        ["Prix du m³ (FCFA)", f"{facture.prix_m3:.2f}"],
        ["Montant total (FCFA)", f"{facture.montant:.2f}"],
    ]
    table_detail = Table(detail, colWidths=[8 * cm, 8 * cm])
    table_detail.setStyle(
        TableStyle(
            [
                ("BACKGROUND", (0, 0), (-1, 0), colors.HexColor("#2563EB")),
                ("TEXTCOLOR", (0, 0), (-1, 0), colors.white),
                ("FONTNAME", (0, 0), (-1, 0), "Helvetica-Bold"),
                ("FONTNAME", (0, 1), (-1, -1), "Helvetica"),
                ("FONTSIZE", (0, 0), (-1, -1), 10),
                (
                    "ROWBACKGROUNDS",
                    (0, 1),
                    (-1, -1),
                    [colors.white, colors.HexColor("#F1F5F9")],
                ),
                ("GRID", (0, 0), (-1, -1), 0.5, colors.HexColor("#CBD5E1")),
                ("BOTTOMPADDING", (0, 0), (-1, -1), 5),
                ("TOPPADDING", (0, 0), (-1, -1), 5),
                ("FONTNAME", (0, -1), (-1, -1), "Helvetica-Bold"),
                ("BACKGROUND", (0, -1), (-1, -1), colors.HexColor("#DBEAFE")),
            ]
        )
    )
    story.append(table_detail)
    story.append(Spacer(1, 0.8 * cm))

    story.append(
        Paragraph(
            "<i>Merci de régler cette facture avant la date limite indiquée ci-dessus.</i>",
            styles["Normal"],
        )
    )

    doc.build(story)

    with open(filepath, "wb") as f:
        f.write(buffer.getvalue())

    return filepath


def lire_pdf(filepath: str) -> bytes:
    """Lit le PDF depuis le système de fichiers et retourne ses octets."""
    with open(filepath, "rb") as f:
        return f.read()
