"""Accès base de données du Facturation Service."""

import datetime
from decimal import Decimal

from django.db.models import Q

from .models import Facture, StatutFacture, Tarif


class TarifRepository:
    """Accès base de données pour les tarifs."""

    def get_actif(self) -> Tarif:
        """Retourne le tarif actif. Lève ObjectDoesNotExist si aucun."""
        return Tarif.objects.get(is_active=True)

    def deactivate_all(self) -> None:
        """Désactive tous les tarifs (avant d'en créer un nouveau)."""
        Tarif.objects.filter(is_active=True).update(is_active=False)

    def create(self, prix_m3: Decimal, date_effet: datetime.date) -> Tarif:
        """Crée un nouveau tarif actif."""
        return Tarif.objects.create(
            prix_m3=prix_m3, date_effet=date_effet, is_active=True
        )

    def save(self, tarif: Tarif) -> Tarif:
        tarif.save()
        return tarif


class FactureRepository:
    """Accès base de données pour les factures."""

    def next_sequence(self, year: int, month: int) -> int:
        """Retourne le prochain numéro de séquence pour l'année/mois."""
        prefix = f"FACT-{year:04d}-{month:02d}-"
        count = Facture.objects.filter(numero_facture__startswith=prefix).count()
        return count + 1

    def build_numero(self, year: int, month: int) -> str:
        """Construit le prochain numéro de facture au format FACT-AAAA-MM-XXXX."""
        seq = self.next_sequence(year, month)
        return f"FACT-{year:04d}-{month:02d}-{seq:04d}"

    def create(
        self,
        abonne_id: str,
        campagne_id: str,
        ancien_index: Decimal,
        nouveau_index: Decimal,
        consommation: Decimal,
        prix_m3: Decimal,
        montant: Decimal,
        date_releve: datetime.date,
        date_limite_paiement: datetime.date,
        numero_facture: str,
    ) -> Facture:
        return Facture.objects.create(
            numero_facture=numero_facture,
            abonne_id=abonne_id,
            campagne_id=campagne_id,
            ancien_index=ancien_index,
            nouveau_index=nouveau_index,
            consommation=consommation,
            prix_m3=prix_m3,
            montant=montant,
            date_releve=date_releve,
            date_limite_paiement=date_limite_paiement,
            statut=StatutFacture.IMPAYEE,
        )

    def get_by_id(self, facture_id: str) -> Facture:
        return Facture.objects.get(id=facture_id)

    def list_by_filters(
        self,
        campagne_id: str = "",
        abonne_id: str = "",
        statut: str = "",
    ) -> list[Facture]:
        qs = Facture.objects.all()
        filters = Q()
        if campagne_id:
            filters &= Q(campagne_id=campagne_id)
        if abonne_id:
            filters &= Q(abonne_id=abonne_id)
        if statut:
            filters &= Q(statut=statut)
        return list(qs.filter(filters).order_by("-date_generation"))

    def update_statut(self, facture: Facture, statut: str) -> Facture:
        facture.statut = statut
        facture.save(update_fields=["statut"])
        return facture

    def update_pdf_path(self, facture: Facture, pdf_path: str) -> Facture:
        facture.pdf_path = pdf_path
        facture.save(update_fields=["pdf_path"])
        return facture
