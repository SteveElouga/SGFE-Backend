"""Modèles PostgreSQL du Facturation Service (docs/ARCHITECTURE.md §8.4)."""

import uuid

from django.db import models


class StatutFacture(models.TextChoices):
    IMPAYEE = "IMPAYEE", "Impayée"
    PARTIELLE = "PARTIELLE", "Partiellement payée"
    PAYEE = "PAYEE", "Payée"


class Tarif(models.Model):
    """Historique des tarifs (prix du m³).

    Un seul tarif peut être actif à la fois (is_active=True).
    La modification du tarif n'affecte jamais les factures déjà générées.
    """

    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    prix_m3 = models.DecimalField(max_digits=10, decimal_places=2)
    date_effet = models.DateField()
    is_active = models.BooleanField(default=True)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        db_table = "tarifs"
        ordering = ["-created_at"]

    def __str__(self) -> str:
        statut = "actif" if self.is_active else "inactif"
        return f"Tarif {self.prix_m3} FCFA/m³ ({statut}, effet {self.date_effet})"


class NatureFacture(models.TextChoices):
    """Ce que la facture constate.

    ``CONSOMMATION`` naît d'un relevé, à la clôture d'une campagne : son montant
    se déduit des index et le PDF l'explique. ``REGULARISATION`` est saisie à la
    main pour constater une dette qui existait avant — un arriéré antérieur à la
    mise en service, par exemple. Elle n'a ni index ni consommation, et son
    montant ne se déduit de rien : il est déclaré.
    """

    CONSOMMATION = "CONSOMMATION", "Consommation relevée"
    REGULARISATION = "REGULARISATION", "Régularisation d'arriéré"


class Facture(models.Model):
    """Facture générée à la clôture d'une campagne.

    Le prix_m3 est copié depuis le tarif actif au moment de la génération —
    jamais de référence directe au tarif, pour préserver l'historique.
    """

    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    # Format : FACT-AAAA-MM-XXXX (ex. FACT-2025-07-0001)
    numero_facture = models.CharField(max_length=30, unique=True)
    # Références externes (pas de FK inter-service)
    abonne_id = models.CharField(max_length=36)
    # Vide pour une facture de régularisation : elle ne naît d'aucune campagne.
    # Le service Paiement l'acceptait déjà (`SoldeFacture.campagne_id` est
    # `blank=True` depuis l'origine) ; c'était Facturation qui l'exigeait.
    campagne_id = models.CharField(max_length=36, blank=True, default="")
    # Index et consommation
    ancien_index = models.DecimalField(max_digits=10, decimal_places=3)
    nouveau_index = models.DecimalField(max_digits=10, decimal_places=3)
    consommation = models.DecimalField(max_digits=10, decimal_places=3)
    # Tarification (valeurs copiées — immuables après génération)
    prix_m3 = models.DecimalField(max_digits=10, decimal_places=2)
    montant = models.DecimalField(max_digits=14, decimal_places=2)
    # Suivi
    statut = models.CharField(max_length=10, choices=StatutFacture.choices, default=StatutFacture.IMPAYEE)
    date_releve = models.DateField()
    date_limite_paiement = models.DateField()
    date_generation = models.DateTimeField(auto_now_add=True)
    pdf_path = models.TextField(blank=True, default="")
    # Version du gabarit ayant produit le PDF stocké (0 = antérieur au versioning).
    # Un écart avec pdf_generator.PDF_TEMPLATE_VERSION déclenche la régénération.
    pdf_template_version = models.PositiveSmallIntegerField(default=0)
    numero_mobile_money = models.CharField(max_length=20, blank=True, default="")
    nature = models.CharField(max_length=16, choices=NatureFacture.choices, default=NatureFacture.CONSOMMATION)
    # Renseigné pour une régularisation : ce que la dette constate, en clair.
    # Imprimé sur le PDF à la place du bloc de relevé.
    motif = models.CharField(max_length=255, blank=True, default="")

    class Meta:
        db_table = "factures"
        indexes = [
            models.Index(fields=["abonne_id"]),
            models.Index(fields=["campagne_id"]),
            models.Index(fields=["statut"]),
        ]

    def __str__(self) -> str:
        return f"{self.numero_facture} — {self.montant} FCFA ({self.statut})"
