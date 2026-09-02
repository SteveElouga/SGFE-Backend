"""Modèles Django du Notification Service.

Deux entités principales :
- Envoi : trace de chaque message WhatsApp envoyé (ou tenté).
- TokenAcces : token UUID partagé dans l'URL de l'espace abonné.
"""

import uuid

from django.db import models


class StatutEnvoi(models.TextChoices):
    EN_ATTENTE = "EN_ATTENTE", "En attente"
    ENVOYE = "ENVOYE", "Envoyé"
    ECHEC = "ECHEC", "Échec"


class TypeEnvoi(models.TextChoices):
    FACTURE = "FACTURE", "Facture"
    RELANCE_1 = "RELANCE_1", "Relance étape 1"
    RELANCE_2 = "RELANCE_2", "Relance étape 2"
    AVERTISSEMENT = "AVERTISSEMENT", "Avertissement"
    SUSPENSION = "SUSPENSION", "Suspension"
    RETABLISSEMENT = "RETABLISSEMENT", "Rétablissement"
    RECU = "RECU", "Reçu"
    # Un versement annulé laisse l'abonné avec un reçu qui ne vaut plus rien, et
    # une dette qu'il croyait éteinte. On le lui dit.
    ANNULATION_PAIEMENT = "ANNULATION_PAIEMENT", "Annulation d'un paiement"
    # Une facture annulée avant tout paiement : rien à rembourser, mais
    # l'abonné qui a déjà reçu le PDF croit toujours la devoir sans ce message.
    ANNULATION_FACTURE = "ANNULATION_FACTURE", "Annulation d'une facture"


class Envoi(models.Model):
    """Trace d'un envoi de message WhatsApp à un abonné."""

    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    facture_id = models.CharField(max_length=36)
    abonne_id = models.CharField(max_length=36)
    type_envoi = models.CharField(max_length=30, choices=TypeEnvoi.choices)
    # Versement dont cet envoi est le reçu — vide pour tout autre type.
    #
    # Sans lui, un reçu ne pouvait pas être renvoyé : rien ne disait de quel
    # versement il était le reçu. Le bouton « Renvoyer » de l'écran de suivi
    # retombait sur `renvoyer_facture`, et l'abonné recevait une facture à la
    # place de son reçu.
    paiement_id = models.CharField(max_length=36, blank=True, default="")
    telephone = models.CharField(max_length=20)
    statut = models.CharField(
        max_length=20,
        choices=StatutEnvoi.choices,
        default=StatutEnvoi.EN_ATTENTE,
    )
    date_envoi = models.DateTimeField(null=True, blank=True)
    # Champ legacy proto — non utilisé (on utilise whatsapp-web.js, pas Telnyx)
    telnyx_message_id = models.CharField(max_length=100, blank=True, default="")
    erreur = models.TextField(blank=True, default="")
    tentatives = models.IntegerField(default=0)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        db_table = "envois"
        indexes = [
            models.Index(fields=["facture_id"]),
            models.Index(fields=["abonne_id"]),
        ]

    def __str__(self) -> str:
        return f"Envoi {self.type_envoi} — {self.abonne_id} — {self.statut}"


class TokenAcces(models.Model):
    """Token UUID partagé dans l'URL publique de l'espace abonné."""

    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    abonne_id = models.CharField(max_length=36)
    facture_id = models.CharField(max_length=36)
    token = models.UUIDField(unique=True, default=uuid.uuid4)
    date_expiration = models.DateField()
    date_derniere_visite = models.DateTimeField(null=True, blank=True)
    is_active = models.BooleanField(default=True)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        db_table = "tokens_acces"
        indexes = [
            models.Index(fields=["token"]),
            models.Index(fields=["abonne_id"]),
        ]

    def __str__(self) -> str:
        statut = "actif" if self.is_active else "révoqué"
        return f"Token {self.token} — abonné {self.abonne_id} — {statut}"
