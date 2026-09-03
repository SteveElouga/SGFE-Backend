"""Accès base de données du Facturation Service."""

import datetime
from decimal import Decimal

from django.db.models import Q, QuerySet

from .models import Facture, NatureFacture, StatutFacture, Tarif


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

    def next_sequence(self, year: int, month: int, for_update: bool = False, serie: str = "FACT") -> int:
        """Retourne le prochain numéro de séquence pour l'année/mois.

        `for_update=True` verrouille la dernière facture du mois (SELECT ...
        FOR UPDATE) pour sérialiser la génération entre transactions
        concurrentes — doit être appelé à l'intérieur du même bloc
        `transaction.atomic()` que la création de la facture (même pattern
        que `abonnes.repositories.AbonneRepository.last_numero`).
        """
        prefix = f"{serie}-{year:04d}-{month:02d}-"
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

    def build_numero(self, year: int, month: int, for_update: bool = False, serie: str = "FACT") -> str:
        """Construit le prochain numéro au format SERIE-AAAA-MM-XXXX.

        Deux séries cohabitent, volontairement distinctes pour qu'aucun
        comptable ne confonde les deux natures :

        - ``FACT`` — consommation relevée, née d'une clôture de campagne ;
        - ``REG``  — régularisation d'un arriéré, saisie à la main, sans relevé.

        Les séquences sont indépendantes : REG-2026-08-0001 peut coexister avec
        FACT-2026-08-0001.
        """
        seq = self.next_sequence(year, month, for_update=for_update, serie=serie)
        return f"{serie}-{year:04d}-{month:02d}-{seq:04d}"

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
        nature: str = NatureFacture.CONSOMMATION,
        motif: str = "",
        remplace_id: str = "",
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
            nature=nature,
            motif=motif,
            remplace_id=remplace_id,
            statut=StatutFacture.IMPAYEE,
        )

    def get_by_id(self, facture_id: str) -> Facture:
        return Facture.objects.get(id=facture_id)

    def _filtres(
        self,
        campagne_id: str = "",
        abonne_id: str = "",
        statut: str = "",
        date_debut: "datetime.date | None" = None,
        date_fin: "datetime.date | None" = None,
    ) -> QuerySet[Facture]:
        """Queryset filtré, partagé par `list_by_filters` et `count_by_filters`
        — le comptage et la page rendue portent ainsi toujours sur les mêmes
        critères, jamais sur la table entière.

        Les bornes de PÉRIODE portent sur `date_generation`, et non sur
        `date_releve` : une régularisation n'a pas de relevé, et c'est la seule
        date que portent les deux natures de facture. Bornes incluses.
        """
        filters = Q()
        if campagne_id:
            filters &= Q(campagne_id=campagne_id)
        if abonne_id:
            filters &= Q(abonne_id=abonne_id)
        if statut:
            filters &= Q(statut=statut)
        if date_debut:
            filters &= Q(date_generation__date__gte=date_debut)
        if date_fin:
            filters &= Q(date_generation__date__lte=date_fin)
        return Facture.objects.filter(filters)

    def list_by_filters(
        self,
        campagne_id: str = "",
        abonne_id: str = "",
        statut: str = "",
        date_debut: "datetime.date | None" = None,
        date_fin: "datetime.date | None" = None,
        limit: int | None = None,
        offset: int | None = None,
    ) -> list[Facture]:
        """Factures filtrées. Tous les critères sont optionnels et se combinent.

        `limit`/`offset` optionnels, appliqués après le tri : omis (`None`),
        la liste complète filtrée est retournée — comportement historique
        préservé à l'identique.
        """
        qs = self._filtres(campagne_id, abonne_id, statut, date_debut, date_fin).order_by("-date_generation")
        if limit is not None or offset is not None:
            start = offset or 0
            qs = qs[start : start + limit] if limit is not None else qs[start:]
        return list(qs)

    def count_by_filters(
        self,
        campagne_id: str = "",
        abonne_id: str = "",
        statut: str = "",
        date_debut: "datetime.date | None" = None,
        date_fin: "datetime.date | None" = None,
    ) -> int:
        """Nombre total de factures correspondant au filtre, indépendamment de
        toute pagination."""
        return self._filtres(campagne_id, abonne_id, statut, date_debut, date_fin).count()

    def update_statut(self, facture: Facture, statut: str) -> Facture:
        facture.statut = statut
        facture.save(update_fields=["statut"])
        return facture

    def update_pdf_path(self, facture: Facture, pdf_path: str, template_version: int) -> Facture:
        """Enregistre le chemin du PDF et la version de gabarit qui l'a produit."""
        facture.pdf_path = pdf_path
        facture.pdf_template_version = template_version
        facture.save(update_fields=["pdf_path", "pdf_template_version"])
        return facture

    def list_historique_consommation(self, abonne_id: str, limit: int = 6) -> list[Facture]:
        """Retourne les `limit` dernières factures de l'abonné, triées chronologiquement.

        Utilisé pour l'histogramme de consommation du PDF de facture (6 derniers
        mois). Une facture par mois en usage normal — pas de déduplication par
        mois nécessaire, chaque abonné n'a qu'un relevé par campagne mensuelle.
        """
        recentes = list(Facture.objects.filter(abonne_id=abonne_id).order_by("-date_releve")[:limit])
        return list(reversed(recentes))
