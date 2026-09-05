import uuid

from django.db import models

from abonnes.fields import EncryptedCharField, EncryptedTextField


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
    # PII chiffrée au repos (voir abonnes/fields.py) — transparent pour le
    # reste du code : ces attributs restent des `str` en clair en Python,
    # seule la colonne en base contient un token Fernet.
    nom = EncryptedCharField(max_length=100)
    prenom = EncryptedCharField(max_length=100)
    telephone_whatsapp = EncryptedCharField(max_length=20)
    adresse = EncryptedTextField(blank=True, default="")
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
    # Emplacement du compteur dans le camp : texte libre (un numéro de
    # parcelle, "3e maison à gauche"...) — quartier/camp regroupent la zone,
    # ce champ précise où chercher *dans* cette zone. Optionnel : les
    # compteurs existants n'en ont pas.
    position = models.CharField(max_length=255, blank=True, default="")
    statut = models.CharField(max_length=20, choices=StatutCompteur.choices, default=StatutCompteur.ACTIF)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        db_table = "compteurs"
        indexes = [models.Index(fields=["abonne"])]
        constraints = [
            # Un seul compteur ACTIF par abonné à la fois (EF-ABO-005) —
            # jusqu'ici garanti uniquement par la logique applicative
            # (remplacer_compteur/resilier_abonne), voir ANO-017.
            models.UniqueConstraint(
                fields=["abonne"],
                condition=models.Q(statut=StatutCompteur.ACTIF),
                name="unique_compteur_actif_par_abonne",
            )
        ]

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
    # Motif du remplacement (ex. « Compteur défectueux »). Optionnel.
    motif = models.CharField(max_length=255, blank=True, default="")
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        db_table = "historique_compteurs"


class AuditLog(models.Model):
    """Journal d'audit append-only des mutations de l'Abonné Service.

    Voir AUDIT_SGFE.md §10.7 (« Conception — propagation d'identité → journal
    d'audit immuable »), déjà implémenté à l'identique pour le Paiement
    Service (`services/paiement/paiements/models.py`). Une ligne par mutation
    métier, écrite par `abonnes.audit.enregistrer_audit` DANS LA MÊME
    transaction Django que le changement qu'elle documente — jamais un appel
    réseau séparé après coup.

    Immuabilité :
    - applicative : aucun code de ce dépôt ne fait d'UPDATE ni de DELETE sur
      ce modèle (`enregistrer_audit` ne fait qu'un `create`) ;
    - défense en profondeur, niveau base : la migration
      `0010_audit_log_immutable` révoque UPDATE/DELETE sur cette table pour
      le rôle applicatif Postgres.
    """

    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    # Verbe métier de la mutation (ex. "ABONNE_CREE", "COMPTEUR_REMPLACE").
    action = models.CharField(max_length=100)
    # Type de l'objet métier concerné (ex. "Abonne", "Compteur").
    objet_type = models.CharField(max_length=100)
    # Identifiant de l'objet métier concerné (UUID le plus souvent, en texte).
    objet_id = models.CharField(max_length=100)
    # Identité de l'appelant (voir `get_caller()`, grpc_interceptors.py) — vide
    # si aucune identité n'a été propagée par la gateway (ne doit plus arriver
    # une fois l'étape 1 déployée partout, mais l'audit ne doit jamais faire
    # échouer la mutation qu'il documente : champs vides plutôt qu'exception).
    acteur_id = models.CharField(max_length=100, blank=True, default="")
    acteur_nom = models.CharField(max_length=150, blank=True, default="")
    acteur_role = models.CharField(max_length=50, blank=True, default="")
    horodatage = models.DateTimeField(auto_now_add=True)
    # Détail libre, lisible par un humain (identifiants, motif...) — pas de
    # structure imposée : ce journal sert la preuve « qui a fait quoi
    # quand », pas une reconstruction programmatique de l'état.
    detail = models.TextField(blank=True, default="")

    class Meta:
        db_table = "audit_log"
        indexes = [
            models.Index(fields=["objet_type", "objet_id"]),
            models.Index(fields=["horodatage"]),
        ]
        ordering = ["-horodatage"]

    def __str__(self) -> str:
        return (
            f"[{self.horodatage}] {self.action} {self.objet_type}={self.objet_id} par {self.acteur_nom or '(inconnu)'}"
        )
