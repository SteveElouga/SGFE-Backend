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


# Plafond de tentatives automatiques du retry WhatsApp (`schedulers.py::
# retry_envois_echec_job`). Au-delà, plus aucune retentative automatique —
# seule une action manuelle (renvoi depuis l'écran de suivi) peut relancer
# l'envoi. Valeur alignée sur `MAX_DELIVERY_ATTEMPTS` (reporting/stats/
# event_consumer.py) : même idée de plafond de redélivrance, sur un autre
# mécanisme (Redis Streams côté reporting, retry applicatif ici).
MAX_TENTATIVES_AUTO = 5


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
    # Texte exact tenté au dernier envoi (premier essai ou retry) — permet au
    # retry automatique de rejouer EXACTEMENT le même message, sans recalculer
    # le message métier : celui-ci peut référencer des données qui ont changé
    # depuis (montant recalculé après un nouveau versement, solde différent…).
    dernier_message = models.TextField(blank=True, default="")
    # Un PDF doit-il être rejoint au prochain retry ? Le PDF lui-même n'est PAS
    # stocké en base (poids potentiellement important) — il est régénéré via
    # le client facturation au moment du retry (voir
    # `EnvoiService._regenerer_pdf_retry`).
    avec_pdf = models.BooleanField(default=False)
    # Nom du fichier PDF à joindre au retry — vide si avec_pdf est False.
    pdf_filename = models.CharField(max_length=255, blank=True, default="")
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        db_table = "envois"
        indexes = [
            models.Index(fields=["facture_id"]),
            models.Index(fields=["abonne_id"]),
        ]

    def __str__(self) -> str:
        return f"Envoi {self.type_envoi} — {self.abonne_id} — {self.statut}"


class StatutDiffusion(models.TextChoices):
    EN_COURS = "EN_COURS", "En cours"
    TERMINEE = "TERMINEE", "Terminée"


class StatutDiffusionEnvoi(models.TextChoices):
    EN_ATTENTE = "EN_ATTENTE", "En attente"
    ENVOYE = "ENVOYE", "Envoyé"
    ECHEC = "ECHEC", "Échec"


class Diffusion(models.Model):
    """Message libre envoyé à un ensemble d'abonnés (annonce, coupure d'eau…).

    Distincte de `Envoi` : celui-ci trace un message métier vers UN abonné,
    déclenché par un événement (facture, paiement, impayé) ; une `Diffusion`
    est composée à la main par un ADMIN et vise potentiellement des dizaines
    d'abonnés à la fois.
    """

    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    message = models.TextField()
    statut = models.CharField(
        max_length=20,
        choices=StatutDiffusion.choices,
        default=StatutDiffusion.EN_COURS,
    )
    # Identifiant Auth Service de l'ADMIN qui a lancé la diffusion — résolu en
    # nom d'utilisateur affichable côté gateway, même pattern que
    # `PaiementResponse.enregistre_par`.
    created_by = models.CharField(max_length=36, blank=True, default="")
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        db_table = "diffusions"

    def __str__(self) -> str:
        return f"Diffusion {self.id} — {self.statut}"


class DiffusionEnvoi(models.Model):
    """Un envoi individuel au sein d'une diffusion — une ligne par abonné visé.

    `nb_total`/`nb_envoyes`/`nb_echecs` ne sont volontairement pas stockés sur
    `Diffusion` : ils se recalculent par agrégation sur ces lignes à chaque
    lecture, pour ne jamais afficher un compteur qui a dérivé de l'état réel.
    """

    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    diffusion = models.ForeignKey(Diffusion, on_delete=models.CASCADE, related_name="envois")
    abonne_id = models.CharField(max_length=36)
    telephone = models.CharField(max_length=20)
    statut = models.CharField(
        max_length=20,
        choices=StatutDiffusionEnvoi.choices,
        default=StatutDiffusionEnvoi.EN_ATTENTE,
    )
    erreur = models.TextField(blank=True, default="")
    date_envoi = models.DateTimeField(null=True, blank=True)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        db_table = "diffusion_envois"
        indexes = [
            models.Index(fields=["diffusion", "statut"]),
            models.Index(fields=["abonne_id"]),
        ]

    def __str__(self) -> str:
        return f"DiffusionEnvoi {self.abonne_id} — {self.statut}"


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
