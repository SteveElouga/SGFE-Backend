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


# Clés de configuration et leurs valeurs par défaut.
#
# Les noms de clés doivent correspondre exactement (même casse) à ceux utilisés
# par les ConfigServiceClient des services consommateurs (facturation, paiement,
# notification) — un écart de casse ou de nom fait échouer GetConfig en
# NOT_FOUND et retombe silencieusement sur la valeur par défaut codée en dur
# côté consommateur (voir ANO-001 dans docs/ETAT_DU_SYSTEME.md). Toute
# nouvelle clé doit être ajoutée ici avec le nom exact utilisé par l'appelant.
#
# Délais de relance impayés alignés sur docs/SRS.md EF-IMP-002 (J+0/J+3/J+7/J+10).
CONFIG_DEFAULTS: dict[str, tuple[str, str]] = {
    "delai_paiement_jours": (
        "5",
        "Nombre de jours accordés pour payer une facture après le relevé",
    ),
    "token_validite_jours": (
        "20",
        "Durée de validité des tokens d'accès abonné (WhatsApp)",
    ),
    "impaye_delai_rappel_1": ("0", "Délai J+X pour l'envoi du rappel doux (étape 1)"),
    "impaye_delai_rappel_2": ("3", "Délai J+X pour le rappel ferme (étape 2)"),
    "impaye_delai_avertissement": ("7", "Délai J+X pour l'avertissement (étape 3)"),
    "impaye_delai_suspension": ("10", "Délai J+X pour la suspension effective (étape 4)"),
    "impaye_suspension_auto": (
        "true",
        "Activer la suspension automatique des abonnés impayés (true/false)",
    ),
    "impaye_suspension_relances": (
        "5",
        "Délai accordé après un versement partiel avant reprise des relances",
    ),
    "email_admin_notifications": (
        "",
        "Adresse email destinataire des notifications administratives (Brevo). Vide = désactivé.",
    ),
    "notifications_admin_activees": (
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


class AuditLog(models.Model):
    """Journal d'audit append-only des mutations du Config Service.

    Voir AUDIT_SGFE.md §10.7 (« Conception — propagation d'identité → journal
    d'audit immuable »). Une ligne par mutation métier, écrite par
    `parametres.audit.enregistrer_audit` DANS LA MÊME transaction Django que
    le changement qu'elle documente — jamais un appel réseau séparé après coup.

    Immuabilité :
    - applicative : aucun code de ce dépôt ne fait d'UPDATE ni de DELETE sur
      ce modèle (`enregistrer_audit` ne fait qu'un `create`) ;
    - défense en profondeur, niveau base : la migration
      `0004_audit_log_immutable` révoque UPDATE/DELETE sur cette table pour
      le rôle applicatif Postgres.
    """

    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    # Verbe métier de la mutation (ex. "CONFIG_PARAM_MODIFIE", "INFOS_SOCIETE_MODIFIEES").
    action = models.CharField(max_length=100)
    # Type de l'objet métier concerné (ex. "ConfigParam", "InfosSociete").
    objet_type = models.CharField(max_length=100)
    # Identifiant de l'objet métier concerné (la clé pour un ConfigParam, "1"
    # pour le singleton InfosSociete).
    objet_id = models.CharField(max_length=100)
    # Identité de l'appelant (voir `get_caller()`, grpc_interceptors.py) — vide
    # si aucune identité n'a été propagée par la gateway (ne doit plus arriver
    # une fois l'étape 1 déployée partout, mais l'audit ne doit jamais faire
    # échouer la mutation qu'il documente : champs vides plutôt qu'exception).
    acteur_id = models.CharField(max_length=100, blank=True, default="")
    acteur_nom = models.CharField(max_length=150, blank=True, default="")
    acteur_role = models.CharField(max_length=50, blank=True, default="")
    horodatage = models.DateTimeField(auto_now_add=True)
    # Détail libre, lisible par un humain (ancienne/nouvelle valeur...) — pas
    # de structure imposée : ce journal sert la preuve « qui a fait quoi
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
