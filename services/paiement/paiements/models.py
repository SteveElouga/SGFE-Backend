"""Modèles de données du Paiement Service."""

import uuid

from django.db import models


class ModePaiement(models.TextChoices):
    ESPECES = "ESPECES", "Espèces"
    MOBILE_MONEY = "MOBILE_MONEY", "Mobile Money"
    VIREMENT = "VIREMENT", "Virement bancaire"


class StatutSolde(models.TextChoices):
    IMPAYEE = "IMPAYEE", "Impayée"
    PARTIELLE = "PARTIELLE", "Partiellement payée"
    PAYEE = "PAYEE", "Payée"


class Paiement(models.Model):
    """Enregistrement d'un versement partiel ou total sur une facture."""

    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    # Référence vers Facturation Service (pas de FK inter-service)
    facture_id = models.CharField(max_length=36)
    # Référence vers Abonné Service (pas de FK inter-service)
    abonne_id = models.CharField(max_length=36)
    montant = models.DecimalField(max_digits=12, decimal_places=2)
    date_paiement = models.DateField()
    mode_paiement = models.CharField(max_length=20, choices=ModePaiement.choices)
    reference_transaction = models.CharField(max_length=100, blank=True, default="")
    # ID utilisateur Auth Service qui a enregistré le paiement
    enregistre_par = models.CharField(max_length=36)
    created_at = models.DateTimeField(auto_now_add=True)
    # Annulation douce : le paiement reste en base, marqué annulé (traçabilité
    # qui/quand/pourquoi). Un paiement annulé ne compte plus dans le solde.
    annule = models.BooleanField(default=False)
    annule_le = models.DateTimeField(null=True, blank=True)
    annule_par = models.CharField(max_length=36, blank=True, default="")
    motif_annulation = models.CharField(max_length=255, blank=True, default="")

    class Meta:
        db_table = "paiements"
        indexes = [models.Index(fields=["facture_id"])]
        ordering = ["-created_at"]
        constraints = [
            # Idempotence : une référence de transaction (MoMo/virement) ne peut
            # correspondre qu'à UN seul paiement — filet anti double-versement
            # (rejeu réseau, double-clic). Les paiements ESPÈCES (référence vide)
            # ne sont pas contraints.
            models.UniqueConstraint(
                fields=["reference_transaction"],
                condition=~models.Q(reference_transaction=""),
                name="unique_reference_transaction_non_vide",
            ),
        ]

    def __str__(self) -> str:
        return f"Paiement {self.montant} — facture {self.facture_id}"


class SoldeFacture(models.Model):
    """Solde courant d'une facture — une ligne par facture (PK = facture_id)."""

    # Clé primaire métier : une ligne par facture
    facture_id = models.CharField(max_length=36, primary_key=True)
    abonne_id = models.CharField(max_length=36)
    # Campagne d'origine (fournie par Facturation à InitialiserSolde) — permet
    # d'agréger les stats de paiement par campagne sans lookup (Reporting, ADR-019).
    campagne_id = models.CharField(max_length=36, blank=True, default="")
    montant_total = models.DecimalField(max_digits=12, decimal_places=2)
    montant_paye = models.DecimalField(max_digits=12, decimal_places=2, default=0)
    solde_restant = models.DecimalField(max_digits=12, decimal_places=2)
    statut = models.CharField(
        max_length=10,
        choices=StatutSolde.choices,
        default=StatutSolde.IMPAYEE,
    )
    date_limite_paiement = models.DateField()
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        db_table = "soldes_factures"

    def __str__(self) -> str:
        return f"Solde facture {self.facture_id} — {self.statut} ({self.solde_restant} restant)"


class SuiviImpaye(models.Model):
    """Suivi des étapes de relance pour une facture impayée."""

    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    # Une seule entrée par facture
    facture_id = models.CharField(max_length=36, unique=True)
    abonne_id = models.CharField(max_length=36)
    date_depassement = models.DateField()
    etape_actuelle = models.IntegerField(default=1)

    # Étape 1 — 1er rappel
    rappel_1_envoye = models.BooleanField(default=False)
    date_rappel_1 = models.DateTimeField(null=True, blank=True)

    # Étape 2 — 2ème rappel
    rappel_2_envoye = models.BooleanField(default=False)
    date_rappel_2 = models.DateTimeField(null=True, blank=True)

    # Étape 3 — Avertissement
    avertissement_envoye = models.BooleanField(default=False)
    date_avertissement = models.DateTimeField(null=True, blank=True)

    # Étape 4 — Suspension
    suspension_effectuee = models.BooleanField(default=False)
    date_suspension = models.DateTimeField(null=True, blank=True)

    # Suspension temporaire des relances (après paiement partiel)
    relances_suspendues_jusqu = models.DateField(null=True, blank=True)

    # Date de résolution (facture payée)
    resolu_le = models.DateField(null=True, blank=True)

    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        db_table = "suivis_impayes"

    def __str__(self) -> str:
        return f"Suivi impayé facture {self.facture_id} — étape {self.etape_actuelle}"
