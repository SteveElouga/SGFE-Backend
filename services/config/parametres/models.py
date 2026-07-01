import uuid

from django.db import models


class InfosSociete(models.Model):
    """Informations de la société (singleton) — apparaissent sur les factures PDF.

    Un seul enregistrement est conservé (id=1). Utiliser InfosSocieteService
    pour accéder/modifier — ne jamais instancier directement.
    """

    nom = models.CharField(max_length=200, default="")
    adresse = models.TextField(default="")
    telephone = models.CharField(max_length=20, default="")
    logo_path = models.CharField(max_length=500, blank=True, default="")
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        db_table = "infos_societe"

    def __str__(self) -> str:
        return self.nom


# Clés de configuration et leurs valeurs par défaut
CONFIG_DEFAULTS: dict[str, tuple[str, str]] = {
    "DELAI_PAIEMENT_JOURS": (
        "5",
        "Nombre de jours accordés pour payer une facture après le relevé",
    ),
    "TOKEN_VALIDITE_JOURS": (
        "20",
        "Durée de validité des tokens d'accès abonné (WhatsApp)",
    ),
    "SUSPENSION_AUTO_ACTIVE": (
        "true",
        "Activer la suspension automatique des abonnés impayés (true/false)",
    ),
    "DELAI_SUSPENSION_APRES_VERSEMENT_JOURS": (
        "5",
        "Délai accordé après un versement partiel avant relance suivante",
    ),
    "RELANCE_ETAPE_1_JOURS": ("0", "Délai J+X pour l'envoi du rappel doux (étape 1)"),
    "RELANCE_ETAPE_2_JOURS": ("3", "Délai J+X pour le rappel ferme (étape 2)"),
    "RELANCE_ETAPE_3_JOURS": ("7", "Délai J+X pour la mise en demeure (étape 3)"),
    "RELANCE_ETAPE_4_JOURS": ("14", "Délai J+X pour la suspension effective (étape 4)"),
    "EMAIL_ADMIN_NOTIFICATIONS": (
        "",
        "Adresse email destinataire des notifications administratives (Brevo). Vide = désactivé.",
    ),
    "NOTIFICATIONS_ADMIN_ACTIVEES": (
        "true",
        "Activer les notifications email aux administrateurs (true/false). false = silence total.",
    ),
}


class ConfigParam(models.Model):
    """Paramètre de configuration clé/valeur.

    Les valeurs sont toujours stockées en texte ; le service appelant est
    responsable de la conversion de type (int, bool, etc.).
    """

    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    cle = models.CharField(max_length=100, unique=True)
    valeur = models.TextField()
    description = models.TextField(blank=True, default="")
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        db_table = "config_params"
        ordering = ["cle"]

    def __str__(self) -> str:
        return f"{self.cle}={self.valeur}"
