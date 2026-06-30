import uuid

from django.db import models


class StatutAbonne(models.TextChoices):
    ACTIF = "ACTIF", "Actif"
    SUSPENDU = "SUSPENDU", "Suspendu"
    RESILIE = "RESILIE", "Résilié"


class StatutCompteur(models.TextChoices):
    ACTIF = "ACTIF", "Actif"
    REMPLACE = "REMPLACE", "Remplacé"
    DESACTIVE = "DESACTIVE", "Désactivé"


class Abonne(models.Model):
    """Mappé sur `abonnes` (docs/ARCHITECTURE.md §8.2)."""

    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    numero_abonne = models.CharField(max_length=10, unique=True, editable=False)
    nom = models.CharField(max_length=100)
    prenom = models.CharField(max_length=100)
    telephone_whatsapp = models.CharField(max_length=20)
    adresse = models.TextField(blank=True, default="")
    statut = models.CharField(max_length=20, choices=StatutAbonne.choices, default=StatutAbonne.ACTIF)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        db_table = "abonnes"
        indexes = [models.Index(fields=["statut"])]

    def __str__(self) -> str:
        return f"{self.numero_abonne} — {self.nom} {self.prenom}"


class Compteur(models.Model):
    """Mappé sur `compteurs` (docs/ARCHITECTURE.md §8.2). Un abonné a
    exactement un compteur ACTIF à la fois (EF-ABO-005)."""

    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    abonne = models.ForeignKey(Abonne, on_delete=models.CASCADE, related_name="compteurs")
    numero_compteur = models.IntegerField(unique=True)
    quartier = models.CharField(max_length=100)
    camp = models.IntegerField()
    index_initial = models.DecimalField(max_digits=10, decimal_places=3, default=0)
    date_pose = models.DateField()
    statut = models.CharField(max_length=20, choices=StatutCompteur.choices, default=StatutCompteur.ACTIF)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        db_table = "compteurs"
        indexes = [models.Index(fields=["abonne"])]

    def __str__(self) -> str:
        return f"Compteur {self.numero_compteur} ({self.abonne.numero_abonne})"


class HistoriqueCompteur(models.Model):
    """Mappé sur `historique_compteurs` — trace chaque remplacement (EF-ABO-006)."""

    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    abonne = models.ForeignKey(Abonne, on_delete=models.CASCADE, related_name="historique_compteurs")
    ancien_compteur = models.ForeignKey(Compteur, on_delete=models.PROTECT, related_name="+")
    nouveau_compteur = models.ForeignKey(Compteur, on_delete=models.PROTECT, related_name="+")
    index_fermeture = models.DecimalField(max_digits=10, decimal_places=3)
    date_remplacement = models.DateField()
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        db_table = "historique_compteurs"
