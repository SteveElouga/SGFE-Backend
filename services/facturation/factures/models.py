"""Modèles PostgreSQL du Facturation Service (docs/ARCHITECTURE.md §8.4)."""

import uuid
from typing import Any

from django.db import models


class StatutFacture(models.TextChoices):
    IMPAYEE = "IMPAYEE", "Impayée"
    PARTIELLE = "PARTIELLE", "Partiellement payée"
    PAYEE = "PAYEE", "Payée"
    # Une facture annulée n'est pas supprimée : elle reste au journal avec son
    # numéro, son motif et la trace de qui l'a annulée. Une numérotation
    # comptable dont des numéros disparaissent n'est plus une numérotation —
    # le trou est précisément ce qui prouve qu'on a effacé quelque chose.
    ANNULEE = "ANNULEE", "Annulée"


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

    # ── Annulation ────────────────────────────────────────────────────────────
    # Le motif est obligatoire, comme il l'est pour une régularisation : ces
    # deux gestes modifient une dette sans qu'aucun index ne le justifie, et la
    # phrase saisie est la seule trace de la raison.
    motif_annulation = models.CharField(max_length=255, blank=True, default="")
    date_annulation = models.DateTimeField(null=True, blank=True)
    annulee_par = models.CharField(max_length=150, blank=True, default="")
    # Facture émise en remplacement, quand l'annulation s'accompagne d'une
    # régénération. Relie les deux bouts de la correction : sans ce lien, le
    # journal montre une facture annulée et une autre née le même jour, sans
    # rien qui dise que la seconde répare la première.
    remplacee_par_id = models.CharField(max_length=36, blank=True, default="")
    # Facture que celle-ci remplace — le lien inverse, pour qu'une facture
    # corrigée puisse citer celle qu'elle corrige sur son PDF.
    remplace_id = models.CharField(max_length=36, blank=True, default="")

    class Meta:
        db_table = "factures"
        indexes = [
            models.Index(fields=["abonne_id"]),
            models.Index(fields=["campagne_id"]),
            models.Index(fields=["statut"]),
            # `list_by_filters` (repositories.py) trie systématiquement par
            # -date_generation, quels que soient les filtres (campagne_id/
            # abonne_id/statut tous optionnels) — c'est l'écran ADMIN/COMPTABLE
            # "factures" du gateway (facturation_queries.py::factures).
            models.Index(fields=["-date_generation"]),
        ]

    def __str__(self) -> str:
        return f"{self.numero_facture} — {self.montant} FCFA ({self.statut})"


class TypeEvenementOutbox(models.TextChoices):
    """Types d'événements portés par l'outbox transactionnelle (voir OutboxEvent).

    Un seul type existe à ce jour — la propagation du solde à la génération
    d'une facture. Le champ reste un simple ``CharField`` (pas une contrainte
    de choix en base) pour qu'un futur type d'événement outbox n'exige pas de
    migration supplémentaire.
    """

    FACTURE_GENEREE = "FACTURE_GENEREE", "Facture générée"


class StatutOutboxEvent(models.TextChoices):
    """États du cycle de vie d'un `OutboxEvent`."""

    EN_ATTENTE = "EN_ATTENTE", "En attente"
    ENVOYE = "ENVOYE", "Envoyé"
    # Terminal : le plafond de tentatives du relais est atteint sans succès —
    # nécessite une intervention manuelle (voir factures/schedulers.py).
    ECHEC = "ECHEC", "Échec définitif"


class OutboxEvent(models.Model):
    """Événement du pattern *transactional outbox* — facturation → paiement.

    Écrit dans LA MÊME transaction Django que la `Facture` qu'il relaie (voir
    `FactureService._ecrire_evenement_outbox_facture_generee`, appelée à
    l'intérieur du même `transaction.atomic()` que `FactureRepository.create`
    dans `generer_factures` / `regenerer_facture` / `creer_regularisation`) :
    soit les deux écritures committent ensemble, soit aucune ne committe. Un
    crash entre la création de la facture et l'appel gRPC à Paiement Service
    ne peut donc plus produire de facture « orpheline » (sans `SoldeFacture`)
    — l'événement survit en base et sera rejoué par le relais planifié
    (`factures/schedulers.py::outbox_relay_job`) jusqu'à ce qu'il réussisse.

    `InitialiserSolde` (Paiement Service) est idempotent par `facture_id` :
    un relais qui rejoue un événement déjà traité (redémarrage entre l'appel
    gRPC et la mise à jour du statut, par exemple) ne duplique jamais le
    solde.
    """

    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    type_evenement = models.CharField(max_length=50, choices=TypeEvenementOutbox.choices)
    # Tout ce dont Paiement Service a besoin pour (ré)initialiser le solde,
    # plus prix_m3 conservé à titre d'audit (traçabilité complète de la
    # facture d'origine, même si InitialiserSolde ne le consomme pas) — voir
    # `FactureService._ecrire_evenement_outbox_facture_generee`.
    payload: "models.JSONField[dict[str, Any], dict[str, Any]]" = models.JSONField()
    statut = models.CharField(
        max_length=10,
        choices=StatutOutboxEvent.choices,
        default=StatutOutboxEvent.EN_ATTENTE,
    )
    tentatives = models.PositiveIntegerField(default=0)
    created_at = models.DateTimeField(auto_now_add=True)
    envoye_at = models.DateTimeField(null=True, blank=True)

    class Meta:
        db_table = "outbox_events"
        ordering = ["created_at"]
        indexes = [
            # Le relais (`outbox_relay_job`) scanne les événements EN_ATTENTE
            # à chaque passage — même usage que `Facture.statut` ci-dessus.
            models.Index(fields=["statut"]),
        ]

    def __str__(self) -> str:
        return f"OutboxEvent {self.type_evenement} ({self.statut}) — {self.id}"


class AuditLog(models.Model):
    """Journal d'audit append-only des mutations du Facturation Service.

    Voir AUDIT_SGFE.md §10.7 (« Conception — propagation d'identité → journal
    d'audit immuable »). Une ligne par mutation métier, écrite par
    `factures.audit.enregistrer_audit` DANS LA MÊME transaction Django que le
    changement qu'elle documente — jamais un appel réseau séparé après coup.

    Immuabilité :
    - applicative : aucun code de ce dépôt ne fait d'UPDATE ni de DELETE sur
      ce modèle (`enregistrer_audit` ne fait qu'un `create`) ;
    - défense en profondeur, niveau base : la migration
      `0008_audit_log_immutable` révoque UPDATE/DELETE sur cette table pour
      le rôle applicatif Postgres — révocation rendue réellement effective
      par `0010_audit_log_role_runtime` (rôle `_runtime` non superutilisateur,
      voir `factures/db_hardening.py` et AUDIT_SGFE.md §8·J).
    """

    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    # Verbe métier de la mutation (ex. "FACTURE_GENEREE", "FACTURE_ANNULEE").
    action = models.CharField(max_length=100)
    # Type de l'objet métier concerné (ex. "Facture", "Tarif").
    objet_type = models.CharField(max_length=100)
    # Identifiant de l'objet métier concerné (UUID le plus souvent, en texte).
    objet_id = models.CharField(max_length=100)
    # Identité de l'appelant (voir `get_caller()`, grpc_interceptors.py) — vide
    # si aucune identité n'a été propagée par la gateway (l'audit ne doit
    # jamais faire échouer la mutation qu'il documente).
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
