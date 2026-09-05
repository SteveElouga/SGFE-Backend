"""Modèles de données du Paiement Service."""

import uuid

from django.db import models


class ModePaiement(models.TextChoices):
    ESPECES = "ESPECES", "Espèces"
    MOBILE_MONEY = "MOBILE_MONEY", "Mobile Money"
    VIREMENT = "VIREMENT", "Virement bancaire"
    # Imputation automatique d'un avoir (report de trop-perçu) — jamais saisi
    # manuellement par un comptable, généré par le service à l'initialisation
    # d'une facture.
    AVOIR = "AVOIR", "Avoir (report de trop-perçu)"


class StatutSolde(models.TextChoices):
    IMPAYEE = "IMPAYEE", "Impayée"
    PARTIELLE = "PARTIELLE", "Partiellement payée"
    PAYEE = "PAYEE", "Payée"
    # Facture annulée : la dette n'existe plus, mais le solde reste au journal.
    # Le distinguer de PAYEE importe — « payée » et « annulée » racontent deux
    # histoires opposées, et les confondre ferait apparaître dans les recettes
    # une somme que personne n'a versée.
    ANNULEE = "ANNULEE", "Annulée"


class Paiement(models.Model):
    """Enregistrement d'un versement partiel ou total sur une facture."""

    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    # Regroupe les écritures nées d'un SEUL versement.
    #
    # Un abonné tend une somme ; elle s'impute sur la facture visée, puis sur
    # ses impayés, et le reste seulement part à l'avoir. Cela produit plusieurs
    # écritures — une par facture touchée — qui ne forment pourtant qu'un
    # versement.
    #
    # Sans ce regroupement, annuler « le paiement » ne défaisait qu'une de ses
    # imputations et laissait les autres debout : un solde faux, exactement le
    # défaut qu'on venait de corriger sur le trop-perçu.
    versement_id = models.UUIDField(default=uuid.uuid4, editable=False)
    # Référence vers Facturation Service (pas de FK inter-service)
    facture_id = models.CharField(max_length=36)
    # Référence vers Abonné Service (pas de FK inter-service)
    abonne_id = models.CharField(max_length=36)
    # Montant REÇU en caisse — pas nécessairement celui imputé à la facture.
    montant = models.DecimalField(max_digits=12, decimal_places=2)
    # Part de `montant` qui n'a pas pu s'imputer et qui est partie à l'avoir de
    # l'abonné (trop-perçu).
    #
    # Sans ce champ, l'annulation était incapable de distinguer les deux : elle
    # rétablissait le solde à partir du montant reçu et laissait l'avoir intact.
    # Un versement de 10 000 sur une facture de 5 000, puis annulé, rendait
    # 10 000 à l'abonné en lui laissant 5 000 de crédit — mesuré.
    montant_excedent = models.DecimalField(max_digits=12, decimal_places=2, default=0)
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
        indexes = [
            models.Index(fields=["facture_id"]),
            # L'annulation charge toutes les écritures d'un versement.
            models.Index(fields=["versement_id"]),
        ]
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
        indexes = [
            # `list_non_soldes_par_abonne` et `total_du_abonne`
            # (repositories.py) filtrent par abonne_id à CHAQUE versement
            # (imputation abonné, calcul du solde antérieur sur un PDF,
            # vérification de réactivation après paiement) — le seul champ de
            # ce modèle sans index jusqu'ici alors qu'il l'est déjà sur
            # Facture/Paiement/MouvementAvoir pour le même usage.
            models.Index(fields=["abonne_id"]),
            # `list_impayes` (repositories.py) scanne TOUTE la table soldes_factures
            # chaque jour (cron ImpayeCheckerJob 8h00, voir schedulers.py) en
            # filtrant sur date_limite_paiement < aujourd'hui.
            models.Index(fields=["date_limite_paiement"]),
        ]

    def __str__(self) -> str:
        return f"Solde facture {self.facture_id} — {self.statut} ({self.solde_restant} restant)"


class AvoirAbonne(models.Model):
    """Crédit (avoir) disponible d'un abonné — une ligne par abonné.

    Alimenté par les trop-perçus (versement supérieur au solde restant) et
    imputé automatiquement sur les prochaines factures de l'abonné, à leur
    initialisation. La contrainte de clé primaire sur `abonne_id` garantit un
    unique solde de crédit par abonné.
    """

    abonne_id = models.CharField(max_length=36, primary_key=True)
    montant = models.DecimalField(max_digits=12, decimal_places=2, default=0)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        db_table = "avoirs_abonnes"

    def __str__(self) -> str:
        return f"Avoir abonné {self.abonne_id} — {self.montant} FCFA"


class TypeMouvementAvoir(models.TextChoices):
    TROP_PERCU = "TROP_PERCU", "Trop-perçu"  # crédit auto (surpaiement)
    RECTIFICATION = "RECTIFICATION", "Rectification"  # crédit manuel (correction / geste commercial)
    IMPUTATION = "IMPUTATION", "Imputation"  # débit (avoir appliqué à une facture)
    # Crédit né de l'annulation d'une facture déjà payée, en tout ou partie.
    # Distinct d'un trop-perçu : l'abonné n'a pas versé de trop, c'est la
    # facture qui a disparu sous son versement.
    ANNULATION = "ANNULATION", "Annulation de facture"
    # Débit né de l'annulation d'un versement en trop-perçu : l'excédent qu'on
    # avait porté au crédit repart, puisque le versement qui l'a produit n'existe
    # plus. Volontairement distinct d'ANNULATION, qui est un crédit — les deux
    # naissent d'une annulation mais vont en sens opposés.
    REPRISE_TROP_PERCU = "REPRISE_TROP_PERCU", "Reprise d'un trop-perçu annulé"


class MouvementAvoir(models.Model):
    """Ligne du journal des mouvements d'avoir d'un abonné (audit du crédit).

    `montant` est toujours positif ; le sens est porté par `type_mouvement`
    (TROP_PERCU / RECTIFICATION = crédit ; IMPUTATION = débit sur une facture).
    """

    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    abonne_id = models.CharField(max_length=36)
    montant = models.DecimalField(max_digits=12, decimal_places=2)
    type_mouvement = models.CharField(max_length=20, choices=TypeMouvementAvoir.choices)
    # Obligatoire pour une RECTIFICATION (correction de facture, geste commercial).
    motif = models.CharField(max_length=255, blank=True, default="")
    # Renseigné pour une IMPUTATION (facture sur laquelle l'avoir a été appliqué).
    facture_id = models.CharField(max_length=36, blank=True, default="")
    # Utilisateur Auth Service pour une RECTIFICATION, "system" pour les mouvements automatiques.
    cree_par = models.CharField(max_length=36, blank=True, default="")
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        db_table = "mouvements_avoir"
        indexes = [models.Index(fields=["abonne_id"])]
        ordering = ["-created_at"]

    def __str__(self) -> str:
        return f"Mouvement avoir {self.type_mouvement} {self.montant} — abonné {self.abonne_id}"


class StatutSessionPaiement(models.TextChoices):
    EN_ATTENTE = "EN_ATTENTE", "En attente"
    CONFIRMEE = "CONFIRMEE", "Confirmée"
    ECHOUEE = "ECHOUEE", "Échouée"
    EXPIREE = "EXPIREE", "Expirée"


class SessionPaiementEnLigne(models.Model):
    """Session de paiement en ligne ouverte depuis l'espace abonné public.

    Paiement en ligne — relance de la décision §10.2 de l'audit, qui l'avait
    écarté. Implémenté ici en mode **sandbox/mock exclusivement** : aucune
    vraie passerelle n'est branchée (voir `passerelle_paiement.py`).

    `id` sert un DOUBLE usage, volontairement : c'est le `session_id` rendu au
    frontend, ET il devient tel quel `reference_transaction` sur le `Paiement`
    créé à la confirmation (voir `PaiementServicer.ConfirmerSessionPaiementEnLigne`).
    La `UniqueConstraint` déjà posée sur ce champ (`Paiement.Meta`) protège donc
    gratuitement contre une double confirmation — inutile d'inventer un
    deuxième mécanisme d'idempotence.
    """

    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    # Référence vers Facturation Service (pas de FK inter-service) — purement
    # informative : l'encaissement à la confirmation impute du plus ancien au
    # plus récent sur TOUT l'abonné (comme `enregistrer_paiement_abonne`), pas
    # spécifiquement sur cette facture. Aucun IDOR possible en la falsifiant :
    # elle ne pilote jamais ce qui est réellement payé.
    facture_id = models.CharField(max_length=36)
    # Résolu depuis `token_espace` (jamais transmis tel quel par l'appelant) —
    # voir `PaiementServicer.CreerSessionPaiementEnLigne`.
    abonne_id = models.CharField(max_length=36)
    montant = models.DecimalField(max_digits=12, decimal_places=2)
    statut = models.CharField(
        max_length=10,
        choices=StatutSessionPaiement.choices,
        default=StatutSessionPaiement.EN_ATTENTE,
    )
    # Token de l'espace abonné qui a créé la session — anti-IDOR : la
    # confirmation exige la présentation de ce MÊME token, sinon la session
    # est traitée comme introuvable (401/404, comme le reste de l'espace
    # abonné — voir ANO-002 sur `espace_abonne_pdf`).
    token_espace = models.CharField(max_length=36)
    created_at = models.DateTimeField(auto_now_add=True)
    # Calculée à la création : `created_at` + 30 minutes (voir
    # `PaiementService.creer_session_paiement_en_ligne`). Une session de
    # paiement en ligne ne doit pas rester valide indéfiniment si l'abonné
    # abandonne le parcours de paiement.
    expire_a = models.DateTimeField()

    class Meta:
        db_table = "sessions_paiement_en_ligne"
        indexes = [
            models.Index(fields=["abonne_id"]),
        ]

    def __str__(self) -> str:
        return f"Session paiement {self.id} — {self.statut} ({self.montant})"


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


class AuditLog(models.Model):
    """Journal d'audit append-only des mutations du Paiement Service.

    Voir AUDIT_SGFE.md §10.7 (« Conception — propagation d'identité → journal
    d'audit immuable »). Une ligne par mutation métier, écrite par
    `paiements.audit.enregistrer_audit` DANS LA MÊME transaction Django que le
    changement qu'elle documente — jamais un appel réseau séparé après coup.

    Immuabilité :
    - applicative : aucun code de ce dépôt ne fait d'UPDATE ni de DELETE sur
      ce modèle (`enregistrer_audit` ne fait qu'un `create`) ;
    - défense en profondeur, niveau base : la migration
      `0013_audit_log_immutable` révoque UPDATE/DELETE sur cette table pour
      le rôle applicatif Postgres — révocation rendue réellement effective
      par `0015_audit_log_role_runtime` (rôle `_runtime` non superutilisateur,
      voir `paiements/db_hardening.py` et AUDIT_SGFE.md §8·J).
    """

    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    # Verbe métier de la mutation (ex. "PAIEMENT_ENREGISTRE", "PAIEMENT_ANNULE").
    action = models.CharField(max_length=100)
    # Type de l'objet métier concerné (ex. "Paiement", "SoldeFacture", "AvoirAbonne").
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
    # Détail libre, lisible par un humain (montants, motif...) — pas de
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
