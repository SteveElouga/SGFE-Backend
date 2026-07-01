import uuid

from django.db import models


class StatutCampagne(models.TextChoices):
    PLANIFIEE = "PLANIFIEE", "Planifiée"
    EN_COURS = "EN_COURS", "En cours"
    CLOTUREE = "CLOTUREE", "Clôturée"


class StatutReleve(models.TextChoices):
    A_RELEVER = "A_RELEVER", "À relever"
    RELEVE = "RELEVE", "Relevé"
    NON_RELEVE = "NON_RELEVE", "Non relevé"
    ESTIME = "ESTIME", "Estimé"


class Campagne(models.Model):
    """Campagne de relevé mensuelle (docs/ARCHITECTURE.md §8.3)."""

    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    nom = models.CharField(max_length=200)
    periode_mois = models.IntegerField()
    periode_annee = models.IntegerField()
    statut = models.CharField(max_length=20, choices=StatutCampagne.choices, default=StatutCampagne.PLANIFIEE)
    date_planifiee = models.DateField(null=True, blank=True)
    # ID de l'utilisateur Auth Service qui a créé la campagne (pour filtrage SUPERVISEUR)
    created_by = models.CharField(max_length=36)
    numero_mobile_money = models.CharField(max_length=20, blank=True, default="")
    date_creation = models.DateTimeField(auto_now_add=True)
    date_cloture = models.DateTimeField(null=True, blank=True)

    class Meta:
        db_table = "campagnes"
        ordering = ["-date_creation"]

    def __str__(self) -> str:
        return f"{self.nom} ({self.periode_mois:02d}/{self.periode_annee})"


class CampagneAgent(models.Model):
    """Affectation d'un AGENT à une campagne — seuls les agents affectés voient et travaillent la campagne."""

    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    campagne = models.ForeignKey(Campagne, on_delete=models.CASCADE, related_name="agents_affectes")
    # ID de l'agent dans Auth Service — pas de FK inter-service
    agent_id = models.CharField(max_length=36)
    date_affectation = models.DateTimeField(auto_now_add=True)

    class Meta:
        db_table = "campagne_agents"
        unique_together = [("campagne", "agent_id")]

    def __str__(self) -> str:
        return f"Agent {self.agent_id} → {self.campagne}"


class Releve(models.Model):
    """Relevé d'index pour un abonné dans une campagne (docs/ARCHITECTURE.md §8.3)."""

    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    campagne = models.ForeignKey(Campagne, on_delete=models.CASCADE, related_name="releves")
    # ID de l'abonné dans Abonné Service — pas de FK inter-service
    abonne_id = models.CharField(max_length=36)
    ancien_index = models.FloatField()
    nouveau_index = models.FloatField(null=True, blank=True)
    consommation = models.FloatField(null=True, blank=True)
    date_releve = models.DateTimeField(null=True, blank=True)
    observation = models.TextField(blank=True, default="")
    statut = models.CharField(max_length=20, choices=StatutReleve.choices, default=StatutReleve.A_RELEVER)
    # ID de l'agent Auth Service qui a saisi le relevé
    agent_id = models.CharField(max_length=36, blank=True, default="")

    class Meta:
        db_table = "releves"
        unique_together = [("campagne", "abonne_id")]

    def __str__(self) -> str:
        return f"Relevé {self.abonne_id} — {self.campagne}"
