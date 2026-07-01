"""Logique métier du Facturation Service."""

import datetime
import logging
from dataclasses import dataclass
from decimal import Decimal, ROUND_HALF_UP

from django.conf import settings
from django.core.exceptions import ObjectDoesNotExist, ValidationError
from django.db import transaction

from .models import Facture, StatutFacture, Tarif
from .pdf_generator import DonneesFacture, InfosSociete, generer_pdf, lire_pdf
from .repositories import FactureRepository, TarifRepository

logger = logging.getLogger(__name__)


@dataclass
class ReleveData:
    """DTO représentant un relevé provenant du Campagne Service."""

    abonne_id: str
    ancien_index: float
    nouveau_index: float
    consommation: float
    date_releve: str  # ISO date "YYYY-MM-DD"


class TarifService:
    """Gestion du tarif actif (prix du m³)."""

    def __init__(self) -> None:
        self._repo = TarifRepository()

    def get_tarif_actuel(self) -> Tarif:
        """Retourne le tarif actif. Lève ObjectDoesNotExist si aucun tarif n'existe."""
        return self._repo.get_actif()

    def update_tarif(self, prix_m3: Decimal, date_effet: datetime.date) -> Tarif:
        """Désactive l'ancien tarif et crée un nouveau tarif actif.

        La modification n'affecte jamais les factures déjà générées —
        chaque facture conserve le prix_m3 copié au moment de sa création.
        """
        if prix_m3 <= Decimal("0"):
            raise ValidationError("Le prix du m³ doit être strictement positif.")
        with transaction.atomic():
            self._repo.deactivate_all()
            return self._repo.create(prix_m3=prix_m3, date_effet=date_effet)


class FactureService:
    """Génération et gestion des factures."""

    def __init__(self) -> None:
        self._repo = FactureRepository()
        self._tarif_repo = TarifRepository()
        # Import tardif pour éviter la circularité au niveau module
        from .grpc_clients import NotificationServiceClient, PaiementServiceClient

        self._paiement_client = PaiementServiceClient()
        self._notification_client = NotificationServiceClient()

    def generer_factures(
        self,
        campagne_id: str,
        releves: list[ReleveData],
        delai_paiement_jours: int,
        societe: InfosSociete,
        numero_mobile_money: str = "",
        envoyer_whatsapp_auto: bool = True,
    ) -> list[Facture]:
        """Génère une facture pour chaque relevé RELEVE (index saisi).

        Les relevés NON_RELEVE et ESTIME sont ignorés — pas de facture sans index.
        Le prix_m3 est lu depuis le tarif actif et copié dans chaque facture.
        """
        try:
            tarif = self._tarif_repo.get_actif()
        except ObjectDoesNotExist as exc:
            raise ValidationError("Aucun tarif actif — configurez un tarif avant de générer des factures.") from exc

        factures: list[Facture] = []

        for releve in releves:
            # Seuls les relevés avec index saisi génèrent une facture
            if releve.nouveau_index is None:
                continue

            date_releve = datetime.date.fromisoformat(releve.date_releve)
            date_limite = date_releve + datetime.timedelta(days=delai_paiement_jours)

            consommation = Decimal(str(releve.consommation)).quantize(Decimal("0.001"), rounding=ROUND_HALF_UP)
            montant = (consommation * tarif.prix_m3).quantize(Decimal("0.01"), rounding=ROUND_HALF_UP)
            annee = date_releve.year
            mois = date_releve.month

            numero = self._repo.build_numero(annee, mois)

            with transaction.atomic():
                facture = self._repo.create(
                    abonne_id=releve.abonne_id,
                    campagne_id=campagne_id,
                    ancien_index=Decimal(str(releve.ancien_index)),
                    nouveau_index=Decimal(str(releve.nouveau_index)),
                    consommation=consommation,
                    prix_m3=tarif.prix_m3,
                    montant=montant,
                    date_releve=date_releve,
                    date_limite_paiement=date_limite,
                    numero_facture=numero,
                    numero_mobile_money=numero_mobile_money,
                )
                pdf_path = self._generer_et_sauver_pdf(facture, societe)
                self._repo.update_pdf_path(facture, pdf_path)
                facture.pdf_path = pdf_path

            # Initialise le solde dans Paiement Service (dégradation gracieuse si KO)
            self._paiement_client.initialiser_solde(
                facture_id=str(facture.id),
                abonne_id=releve.abonne_id,
                montant_total=float(facture.montant),
                date_limite_paiement=date_limite.isoformat(),
            )
            # Envoi WhatsApp si activé sur la campagne (dégradation gracieuse si KO)
            if envoyer_whatsapp_auto:
                self._notification_client.envoyer_facture(
                    facture_id=str(facture.id),
                    abonne_id=releve.abonne_id,
                )
            factures.append(facture)

        logger.info(
            "Factures générées pour la campagne",
            extra={"campagne_id": campagne_id, "count": len(factures)},
        )
        return factures

    def _generer_et_sauver_pdf(self, facture: Facture, societe: InfosSociete) -> str:
        """Génère le PDF et retourne son chemin. En cas d'erreur, log et retourne ''."""
        try:
            donnees = DonneesFacture(
                numero_facture=facture.numero_facture,
                abonne_id=str(facture.abonne_id),
                campagne_id=str(facture.campagne_id),
                ancien_index=facture.ancien_index,
                nouveau_index=facture.nouveau_index,
                consommation=facture.consommation,
                prix_m3=facture.prix_m3,
                montant=facture.montant,
                statut=facture.statut,
                date_releve=facture.date_releve.isoformat(),
                date_limite_paiement=facture.date_limite_paiement.isoformat(),
                date_generation=facture.date_generation.strftime("%d/%m/%Y %H:%M"),
            )
            return generer_pdf(donnees, societe, settings.PDF_STORAGE_DIR)
        except Exception:
            logger.exception(
                "Erreur lors de la génération PDF",
                extra={"facture_id": str(facture.id)},
            )
            return ""

    def get_facture(self, facture_id: str) -> Facture:
        """Retourne une facture par son ID. Lève ObjectDoesNotExist si introuvable."""
        return self._repo.get_by_id(facture_id)

    def list_factures(
        self,
        campagne_id: str = "",
        abonne_id: str = "",
        statut: str = "",
    ) -> list[Facture]:
        """Retourne les factures filtrées. Tous les paramètres sont optionnels."""
        if statut and statut not in StatutFacture.values:
            raise ValidationError(f"Statut invalide : {statut}. Valeurs attendues : {', '.join(StatutFacture.values)}")
        return self._repo.list_by_filters(campagne_id=campagne_id, abonne_id=abonne_id, statut=statut)

    def update_statut(self, facture_id: str, statut: str) -> Facture:
        """Met à jour le statut d'une facture (appelé par Paiement Service)."""
        if statut not in StatutFacture.values:
            raise ValidationError(f"Statut invalide : {statut}. Valeurs attendues : {', '.join(StatutFacture.values)}")
        facture = self._repo.get_by_id(facture_id)
        return self._repo.update_statut(facture, statut)

    def get_pdf_bytes(self, facture_id: str) -> tuple[bytes, str]:
        """Retourne le contenu PDF et le nom du fichier pour une facture.

        Génère le PDF à la volée si le chemin n'est pas encore enregistré.
        """
        facture = self._repo.get_by_id(facture_id)
        if facture.pdf_path and __import__("os").path.exists(facture.pdf_path):
            return lire_pdf(facture.pdf_path), f"{facture.numero_facture}.pdf"

        # Régénération à la volée (PDF manquant ou chemin vide)
        from .grpc_clients import ConfigServiceClient

        client = ConfigServiceClient()
        societe = client.get_infos_societe()
        pdf_path = self._generer_et_sauver_pdf(facture, societe)
        if pdf_path:
            self._repo.update_pdf_path(facture, pdf_path)

        if not pdf_path or not __import__("os").path.exists(pdf_path):
            raise FileNotFoundError(f"Impossible de générer le PDF pour la facture {facture_id}.")

        return lire_pdf(pdf_path), f"{facture.numero_facture}.pdf"
