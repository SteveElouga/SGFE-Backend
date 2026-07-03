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
        return Tarif.objects.create(prix_m3=prix_m3, date_effet=date_effet, is_active=True)

    def save(self, tarif: Tarif) -> Tarif:
        tarif.save()
        return tarif


class FactureRepository:
    """Accès base de données pour les factures."""

    def next_sequence(self, year: int, month: int, for_update: bool = False) -> int:
        """Retourne le prochain numéro de séquence pour l'année/mois.

        `for_update=True` verrouille la dernière facture du mois (SELECT ...
        FOR UPDATE) pour sérialiser la génération entre transactions
        concurrentes — doit être appelé à l'intérieur du même bloc
        `transaction.atomic()` que la création de la facture (même pattern
        que `abonnes.repositories.AbonneRepository.last_numero`).
        """
        prefix = f"FACT-{year:04d}-{month:02d}-"
        qs = Facture.objects.select_for_update() if for_update else Facture.objects.all()
        last_numero = (
            qs.filter(numero_facture__startswith=prefix)
            .order_by("-numero_facture")
            .values_list("numero_facture", flat=True)
            .first()
        )
        if not last_numero:
            return 1
        return int(last_numero.rsplit("-", 1)[-1]) + 1

    def build_numero(self, year: int, month: int, for_update: bool = False) -> str:
        """Construit le prochain numéro de facture au format FACT-AAAA-MM-XXXX."""
        seq = self.next_sequence(year, month, for_update=for_update)
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
        numero_mobile_money: str = "",
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
            numero_mobile_money=numero_mobile_money,
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
