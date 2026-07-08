"""Modèles dénormalisés du Reporting Service (reporting_db, docs/ARCHITECTURE.md §8.7).

Tables pré-calculées, alimentées par les événements des autres services via les
RPC Update*. Aucune écriture métier directe : c'est le côté Query du CQRS (ADR-019).
Chaque table est clé par `campagne_id` (une ligne de stats par campagne).
"""

from django.db import models


class StatsCampagne(models.Model):
    """Statistiques de relevé agrégées par campagne."""

    campagne_id = models.UUIDField(primary_key=True)
    nom_campagne = models.CharField(max_length=100, default="")
    total_abonnes = models.IntegerField(default=0)
    nb_releves = models.IntegerField(default=0)
    nb_en_attente = models.IntegerField(default=0)
    nb_estimes = models.IntegerField(default=0)
    nb_non_releves = models.IntegerField(default=0)
    pourcentage_progression = models.DecimalField(max_digits=5, decimal_places=2, default=0)
    consommation_totale = models.DecimalField(max_digits=12, decimal_places=3, default=0)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        db_table = "stats_campagnes"

    def __str__(self) -> str:
        return f"StatsCampagne {self.nom_campagne} ({self.pourcentage_progression}%)"


class StatsFacturation(models.Model):
    """Statistiques de facturation agrégées par campagne."""

    campagne_id = models.UUIDField(primary_key=True)
    total_factures = models.IntegerField(default=0)
    montant_total_facture = models.DecimalField(max_digits=14, decimal_places=2, default=0)
    nb_factures_envoyees = models.IntegerField(default=0)
    nb_factures_payees = models.IntegerField(default=0)
    nb_factures_partielles = models.IntegerField(default=0)
    nb_factures_impayees = models.IntegerField(default=0)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        db_table = "stats_facturation"

    def __str__(self) -> str:
        return f"StatsFacturation campagne={self.campagne_id} ({self.total_factures} factures)"


class StatsPaiements(models.Model):
    """Statistiques de paiement/recouvrement agrégées par campagne."""

    campagne_id = models.UUIDField(primary_key=True)
    montant_encaisse = models.DecimalField(max_digits=14, decimal_places=2, default=0)
    montant_impaye = models.DecimalField(max_digits=14, decimal_places=2, default=0)
    nb_impayes = models.IntegerField(default=0)
    taux_recouvrement = models.DecimalField(max_digits=5, decimal_places=2, default=0)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        db_table = "stats_paiements"

    def __str__(self) -> str:
        return f"StatsPaiements campagne={self.campagne_id} ({self.taux_recouvrement}%)"


class ProcessedEvent(models.Model):
    """Événements déjà appliqués — garantit l'idempotence du consumer.

    Le flux Redis est en livraison **at-least-once** : un même événement peut
    être redélivré (crash entre l'application et le XACK). Pour les stats à
    incrément (`+= delta`), un rejeu doublerait le compteur — on déduplique donc
    par `event_id` (généré par le producteur), appliqué dans la même transaction
    que la mise à jour des stats.
    """

    event_id = models.CharField(max_length=64, primary_key=True)
    event_type = models.CharField(max_length=40)
    processed_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        db_table = "reporting_processed_events"

    def __str__(self) -> str:
        return f"{self.event_type} {self.event_id}"
