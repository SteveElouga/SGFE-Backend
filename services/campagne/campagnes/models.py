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
    # Comportements à la clôture — configurables à la création par le superviseur
    generer_factures_auto = models.BooleanField(default=True)
    envoyer_whatsapp_auto = models.BooleanField(default=True)
    date_creation = models.DateTimeField(auto_now_add=True)
    date_cloture = models.DateTimeField(null=True, blank=True)
    # Vrai si la clôture a échoué à notifier Facturation Service (gRPC
    # injoignable à cet instant précis) : la campagne est bien CLOTUREE, mais
    # la génération des factures n'a jamais été déclenchée. Le job de retry
    # (schedulers.py::facturation_retry_job) rejoue périodiquement la même
    # notification jusqu'à ce qu'elle réussisse, puis remet ce marqueur à faux.
    facturation_en_attente = models.BooleanField(default=False)

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
    # Decimal (pas float) : même convention que les montants d'argent du
    # projet (voir CLAUDE.md racine) — un index de compteur est une mesure
    # exacte, pas une valeur flottante approximée.
    ancien_index = models.DecimalField(max_digits=10, decimal_places=3)
    nouveau_index = models.DecimalField(max_digits=10, decimal_places=3, null=True, blank=True)
    consommation = models.DecimalField(max_digits=10, decimal_places=3, null=True, blank=True)
    date_releve = models.DateTimeField(null=True, blank=True)
    observation = models.TextField(blank=True, default="")
    statut = models.CharField(max_length=20, choices=StatutReleve.choices, default=StatutReleve.A_RELEVER)
    # ID de l'agent Auth Service qui a saisi le relevé
    agent_id = models.CharField(max_length=36, blank=True, default="")
    # Zone du compteur de l'abonné, copiée à la création du relevé (snapshot) :
    # permet de ventiler les relevés par zone sans requête inter-services.
    quartier = models.CharField(max_length=100, blank=True, default="")
    camp = models.IntegerField(null=True, blank=True)

    class Meta:
        db_table = "releves"
        unique_together = [("campagne", "abonne_id")]

    def __str__(self) -> str:
        return f"Relevé {self.abonne_id} — {self.campagne}"


class ActionAudit(models.TextChoices):
    SAISIE = "SAISIE", "Saisie"
    CORRECTION = "CORRECTION", "Correction"


class ReleveAudit(models.Model):
    """Journal d'audit d'un relevé : une entrée par saisie/correction d'index.

    L'auteur est stocké en **snapshot** (id + username + rôle au moment de
    l'action) — un journal d'audit ne doit pas changer si l'utilisateur est
    renommé ou change de rôle plus tard.
    """

    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    releve = models.ForeignKey(Releve, on_delete=models.CASCADE, related_name="audits")
    action = models.CharField(max_length=20, choices=ActionAudit.choices)
    auteur_id = models.CharField(max_length=36)
    auteur_username = models.CharField(max_length=150, blank=True, default="")
    auteur_role = models.CharField(max_length=20, blank=True, default="")
    # Index avant/après l'action, pour tracer la valeur corrigée. Decimal,
    # cohérent avec Releve.ancien_index/nouveau_index (même quantité).
    ancien_index = models.DecimalField(max_digits=10, decimal_places=3, null=True, blank=True)
    nouvel_index = models.DecimalField(max_digits=10, decimal_places=3, null=True, blank=True)
    horodatage = models.DateTimeField(auto_now_add=True)

    class Meta:
        db_table = "releve_audits"
        ordering = ["horodatage"]
        indexes = [models.Index(fields=["releve"])]

    def __str__(self) -> str:
        return f"{self.action} — relevé {self.releve_id} par {self.auteur_username or self.auteur_id}"


class AffectationZone(models.Model):
    """Affectation d'un agent à une zone (quartier + camp) d'une campagne.

    Coexiste avec ``CampagneAgent`` (affectation globale à la campagne) : un
    agent peut être affecté globalement et/ou à des zones précises. Une zone
    donnée d'une campagne appartient à **un seul** agent (unique_together).
    """

    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    campagne = models.ForeignKey(Campagne, on_delete=models.CASCADE, related_name="zones_affectees")
    # ID de l'agent dans Auth Service — pas de FK inter-service
    agent_id = models.CharField(max_length=36)
    quartier = models.CharField(max_length=100)
    camp = models.IntegerField()
    date_affectation = models.DateTimeField(auto_now_add=True)

    class Meta:
        db_table = "affectation_zones"
        unique_together = [("campagne", "quartier", "camp")]
        indexes = [models.Index(fields=["campagne", "agent_id"])]

    def __str__(self) -> str:
        return f"{self.quartier}·{self.camp} → agent {self.agent_id} ({self.campagne_id})"


class RegenerationFactureEnAttente(models.Model):
    """Régénération de facture en attente après correction d'un relevé déjà facturé.

    Créée quand ``CorrigerReleve`` détecte qu'une facture existe déjà pour cet
    abonné dans cette campagne, mais que Facturation Service est resté
    injoignable au moment de déclencher ``RegenererFacture`` (gRPC). La
    correction du relevé elle-même est déjà validée en base à cet instant —
    seule sa répercussion sur la facture est différée.

    Le job de retry (``schedulers.py::facturation_retry_job``, même verrou
    consultatif que le rattrapage de clôture) rejoue périodiquement le même
    chemin de régénération jusqu'à ce qu'il réussisse, puis supprime l'entrée.
    Une seule entrée par (campagne, abonné) : une nouvelle correction avant que
    la précédente n'ait été rejouée remplace simplement le motif/l'auteur —
    la régénération relit de toute façon le relevé courant, pas une valeur
    figée à la création de l'entrée.
    """

    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    campagne = models.ForeignKey(Campagne, on_delete=models.CASCADE, related_name="regenerations_facture_en_attente")
    # ID de l'abonné dans Abonné Service — pas de FK inter-service
    abonne_id = models.CharField(max_length=36)
    motif = models.CharField(max_length=255)
    # ID de l'auteur de la correction (Auth Service) — transmis à Facturation
    # Service comme `regenere_par` lors du rejeu.
    demande_par = models.CharField(max_length=36, blank=True, default="")
    date_creation = models.DateTimeField(auto_now_add=True)
    derniere_tentative = models.DateTimeField(null=True, blank=True)
    nb_tentatives = models.PositiveIntegerField(default=0)

    class Meta:
        db_table = "regenerations_facture_en_attente"
        unique_together = [("campagne", "abonne_id")]

    def __str__(self) -> str:
        return f"Régénération en attente — abonné {self.abonne_id} ({self.campagne_id})"
