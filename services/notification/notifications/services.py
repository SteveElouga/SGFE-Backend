"""Logique métier du Notification Service.

EnvoiService : envoi et suivi des messages WhatsApp.
TokenService : gestion des tokens d'accès à l'espace abonné.
"""

import logging
from datetime import date, timedelta

import grpc
from django.conf import settings
from django.core.exceptions import ValidationError
from django.utils import timezone

from notifications.brevo_client import envoyer_email_admin
from notifications.grpc_clients import abonne_client, config_client, facturation_client
from notifications.message_builder import (
    build_message_facture,
    build_message_relance_1,
    build_message_relance_2,
    build_message_relance_3,
    build_message_relance_4,
    build_message_retablissement,
)
from notifications.models import Envoi, StatutEnvoi, TokenAcces, TypeEnvoi
from notifications.repositories import EnvoiRepository, TokenAccesRepository
from notifications.whatsapp_client import WhatsAppDeliveryError, whatsapp_client

logger = logging.getLogger(__name__)

# Correspondance étape → TypeEnvoi
# Étape 0 = confirmation de paiement / rétablissement (EF-IMP-005), envoyée
# par Paiement Service lorsqu'une facture passe au statut PAYÉE.
_ETAPE_TO_TYPE: dict[int, str] = {
    0: TypeEnvoi.RETABLISSEMENT,
    1: TypeEnvoi.RELANCE_1,
    2: TypeEnvoi.RELANCE_2,
    3: TypeEnvoi.AVERTISSEMENT,
    4: TypeEnvoi.SUSPENSION,
}


def _format_date_fr(date_str: str) -> str:
    """Convertit 'YYYY-MM-DD' en 'JJ/MM/AAAA'."""
    if not date_str:
        return ""
    try:
        parts = date_str[:10].split("-")
        return f"{parts[2]}/{parts[1]}/{parts[0]}"
    except (IndexError, ValueError):
        return date_str


def _periode_from_date(date_str: str) -> str:
    """Extrait 'Mois AAAA' depuis une date ISO (utilisé pour la période de facture)."""
    _MOIS = {
        "01": "Janvier",
        "02": "Février",
        "03": "Mars",
        "04": "Avril",
        "05": "Mai",
        "06": "Juin",
        "07": "Juillet",
        "08": "Août",
        "09": "Septembre",
        "10": "Octobre",
        "11": "Novembre",
        "12": "Décembre",
    }
    try:
        parts = date_str[:10].split("-")
        return f"{_MOIS.get(parts[1], parts[1])} {parts[0]}"
    except (IndexError, ValueError):
        return date_str


class EnvoiService:
    """Logique métier d'envoi de messages WhatsApp."""

    def __init__(self) -> None:
        self._envois = EnvoiRepository()
        self._tokens = TokenAccesRepository()

    def get_whatsapp_qr(self) -> tuple[bool, str, str]:
        """Retourne (ready, qr_data_url, number) pour l'affichage admin de la liaison WhatsApp.

        `number` est le numéro du compte appairé (vide si non connecté).
        Dégradation gracieuse : si le service whatsapp-web.js est inaccessible,
        retourne (False, "", "") plutôt que de lever — l'UI admin affiche alors
        « non connecté » sans erreur bloquante.
        """
        try:
            return whatsapp_client.get_qr()
        except WhatsAppDeliveryError as exc:
            logger.warning("QR WhatsApp indisponible : %s", exc)
            return (False, "", "")

    def tester_envoi(self, phone_number: str) -> None:
        """Envoie un message de test WhatsApp au numéro fourni (écran d'administration).

        Raises:
            ValueError: Si le numéro est vide.
            WhatsAppDeliveryError: Si l'envoi échoue (WhatsApp non connecté,
                numéro invalide, service injoignable).
        """
        if not phone_number:
            raise ValueError("Le numéro de téléphone est obligatoire")
        whatsapp_client.send(
            phone_number,
            "✅ Test SGFE : la connexion WhatsApp fonctionne. "
            "Ce message confirme que l'envoi automatique est opérationnel.",
        )

    def envoyer_facture(self, facture_id: str, abonne_id: str) -> Envoi:
        """Récupère les infos facture/abonné, génère un token, envoie le message WhatsApp.

        En cas d'échec WhatsApp, l'envoi est marqué ECHEC (pas d'exception levée).
        Cela permet une dégradation gracieuse : la facture existe même si le
        message n'a pas été remis.
        """
        # Récupération des données depuis les services amont. Si Facturation ou
        # Abonné est injoignable, on dégrade en un Envoi ECHEC (contrat du
        # servicer : jamais de RpcError brute remontée à l'appelant) au lieu de
        # laisser l'appel échouer en UNKNOWN avant même de créer l'Envoi.
        try:
            facture = facturation_client.get_facture(facture_id)
            abonne = abonne_client.get_abonne(abonne_id)
        except grpc.RpcError as exc:
            return self._echec_amont(facture_id, abonne_id, TypeEnvoi.FACTURE, exc)
        validite_jours = config_client.get_token_validite_jours()

        prenom_nom = f"{abonne.prenom} {abonne.nom.upper()}"
        telephone = abonne.telephone_whatsapp
        periode = _periode_from_date(facture.date_releve)
        date_limite_fr = _format_date_fr(facture.date_limite_paiement)

        # Création du token d'accès
        date_expiration = date.today() + timedelta(days=validite_jours)
        token_obj = self._tokens.create(
            abonne_id=abonne_id,
            facture_id=facture_id,
            date_expiration=date_expiration,
        )
        token_str = str(token_obj.token)
        date_expiration_fr = date_expiration.strftime("%d/%m/%Y")
        frontend_url = settings.FRONTEND_URL

        message = build_message_facture(
            prenom_nom=prenom_nom,
            periode=periode,
            consommation=facture.consommation,
            montant=facture.montant,
            date_limite=date_limite_fr,
            token=token_str,
            date_expiration_token=date_expiration_fr,
            frontend_url=frontend_url,
            numero_mobile_money=facture.numero_mobile_money,
        )

        envoi = self._envois.create(
            facture_id=facture_id,
            abonne_id=abonne_id,
            type_envoi=TypeEnvoi.FACTURE,
            telephone=telephone,
        )

        # Récupération du PDF depuis Facturation Service (dégradation gracieuse si KO)
        pdf_bytes, pdf_filename = facturation_client.get_facture_pdf(facture_id)

        return self._tenter_envoi(envoi, telephone, message, pdf_bytes=pdf_bytes, pdf_filename=pdf_filename)

    def renvoyer_facture(self, facture_id: str) -> Envoi:
        """Révoque l'ancien token, crée un nouveau, et renvoie la facture.

        Utile lorsque le lien précédent a expiré ou a été révoqué.
        """
        # Récupération de la facture pour obtenir l'abonne_id
        facture = facturation_client.get_facture(facture_id)
        abonne_id = facture.abonne_id

        # Révocation des anciens tokens actifs de cette facture
        anciens_tokens = self._tokens.list_active_by_facture(facture_id)
        for tok in anciens_tokens:
            tok.is_active = False
            self._tokens.save(tok)

        # Renvoi avec un nouveau token
        return self.envoyer_facture(facture_id, abonne_id)

    def envoyer_relance(self, facture_id: str, abonne_id: str, etape: int) -> Envoi:
        """Envoie le message de relance (ou de rétablissement) correspondant à l'étape (0 à 4).

        Args:
            facture_id: Identifiant de la facture.
            abonne_id: Identifiant de l'abonné.
            etape: Étape de relance (0 = confirmation de paiement / rétablissement,
                   1 = rappel doux, 2 = rappel ferme, 3 = avertissement, 4 = suspension).

        Raises:
            ValidationError: Si l'étape est hors de la plage [0, 4].
        """
        if etape not in _ETAPE_TO_TYPE:
            raise ValidationError(f"Étape de relance invalide : {etape}. Les étapes valides sont 0, 1, 2, 3 et 4.")

        # Même dégradation gracieuse que envoyer_facture : un service amont
        # injoignable donne un Envoi ECHEC, pas une RpcError brute.
        try:
            facture = facturation_client.get_facture(facture_id)
            abonne = abonne_client.get_abonne(abonne_id)
        except grpc.RpcError as exc:
            return self._echec_amont(facture_id, abonne_id, _ETAPE_TO_TYPE[etape], exc)

        prenom_nom = f"{abonne.prenom} {abonne.nom.upper()}"
        telephone = abonne.telephone_whatsapp
        periode = _periode_from_date(facture.date_releve)

        if etape == 0:
            message = build_message_retablissement(
                prenom_nom=prenom_nom,
                montant=facture.montant,
            )
        elif etape == 1:
            # Pour la relance 1, on inclut le lien de l'espace abonné
            tokens_actifs = self._tokens.list_active_by_facture(facture_id)
            if tokens_actifs:
                token_str = str(tokens_actifs[0].token)
            else:
                # Crée un nouveau token si aucun actif n'existe
                validite_jours = config_client.get_token_validite_jours()
                token_obj = self._tokens.create(
                    abonne_id=abonne_id,
                    facture_id=facture_id,
                    date_expiration=date.today() + timedelta(days=validite_jours),
                )
                token_str = str(token_obj.token)
            lien = f"{settings.FRONTEND_URL}/espace/{token_str}"
            message = build_message_relance_1(
                prenom_nom=prenom_nom,
                periode=periode,
                montant=facture.montant,
                lien_espace=lien,
            )
        elif etape == 2:
            message = build_message_relance_2(
                prenom_nom=prenom_nom,
                periode=periode,
                montant=facture.montant,
            )
        elif etape == 3:
            message = build_message_relance_3(
                prenom_nom=prenom_nom,
                montant=facture.montant,
            )
        else:  # etape == 4
            try:
                infos = config_client.get_infos_societe()
                telephone_societe = infos.telephone
            except Exception:
                telephone_societe = ""
            message = build_message_relance_4(
                prenom_nom=prenom_nom,
                montant=facture.montant,
                periode=periode,
                telephone_societe=telephone_societe,
            )

        type_envoi = _ETAPE_TO_TYPE[etape]
        envoi = self._envois.create(
            facture_id=facture_id,
            abonne_id=abonne_id,
            type_envoi=type_envoi,
            telephone=telephone,
        )

        return self._tenter_envoi(envoi, telephone, message)

    def get_envoi(self, envoi_id: str) -> Envoi:
        """Récupère un envoi par son UUID.

        Raises:
            ObjectDoesNotExist: Si l'envoi est introuvable.
        """
        return self._envois.get_by_id(envoi_id)

    def list_envois(self, facture_id: str, abonne_id: str) -> list[Envoi]:
        """Liste les envois filtrés par facture_id et/ou abonne_id."""
        return self._envois.list_by_facture_and_abonne(facture_id, abonne_id)

    def _tenter_envoi(
        self,
        envoi: Envoi,
        telephone: str,
        message: str,
        pdf_bytes: bytes = b"",
        pdf_filename: str = "",
    ) -> Envoi:
        """Tente l'envoi WhatsApp et met à jour le statut de l'envoi.

        Si pdf_bytes est fourni, envoie le PDF en pièce jointe via /send-with-pdf.
        En cas de WhatsAppDeliveryError, l'envoi est marqué ECHEC (dégradation gracieuse).
        """
        envoi.tentatives += 1
        try:
            if pdf_bytes:
                whatsapp_client.send_with_pdf(telephone, message, pdf_bytes, pdf_filename or "facture.pdf")
            else:
                whatsapp_client.send(telephone, message)
            envoi.statut = StatutEnvoi.ENVOYE
            envoi.date_envoi = timezone.now()
            logger.info(
                "Message WhatsApp envoyé",
                extra={
                    "envoi_id": str(envoi.id),
                    "facture_id": envoi.facture_id,
                    "type_envoi": envoi.type_envoi,
                },
            )
        except WhatsAppDeliveryError as exc:
            envoi.statut = StatutEnvoi.ECHEC
            envoi.erreur = str(exc)
            logger.warning(
                "Échec envoi WhatsApp",
                extra={
                    "envoi_id": str(envoi.id),
                    "facture_id": envoi.facture_id,
                    "erreur": str(exc),
                },
            )
            # EF-NOTIF-005 — Notifier les admins de chaque échec WhatsApp
            notifier_admins(
                evenement="ECHEC_WHATSAPP",
                detail=f"Échec envoi WhatsApp facture {envoi.facture_id} : {exc}",
                entite_id=envoi.facture_id,
            )
        self._envois.save(envoi)
        return envoi

    def _echec_amont(self, facture_id: str, abonne_id: str, type_envoi: str, exc: grpc.RpcError) -> Envoi:
        """Enregistre un Envoi ECHEC quand un service amont (Facturation/Abonné)
        est injoignable, au lieu de laisser remonter une RpcError brute.

        Garantit le contrat de dégradation gracieuse du servicer : l'appelant
        reçoit toujours un EnvoiResponse (ici ECHEC), jamais une erreur gRPC.
        """
        details = exc.details() if hasattr(exc, "details") else str(exc)
        erreur = f"Service amont injoignable : {details}"
        envoi = self._envois.create(
            facture_id=facture_id,
            abonne_id=abonne_id,
            type_envoi=type_envoi,
            telephone="",
        )
        envoi.statut = StatutEnvoi.ECHEC
        envoi.erreur = erreur
        self._envois.save(envoi)
        logger.warning(
            "Échec récupération des données amont — envoi marqué ECHEC",
            extra={"facture_id": facture_id, "abonne_id": abonne_id, "erreur": erreur},
        )
        try:
            notifier_admins(
                evenement="ECHEC_WHATSAPP",
                detail=f"Échec envoi (données amont indisponibles) facture {facture_id} : {erreur}",
                entite_id=facture_id,
            )
        except Exception:
            logger.warning("Notification admin de l'échec amont impossible", exc_info=True)
        return envoi


def notifier_admins(evenement: str, detail: str, entite_id: str = "") -> None:
    """Envoie un email de notification aux administrateurs via Brevo.

    Récupère l'email destinataire depuis Config Service (clé email_admin_notifications).
    Respecte le toggle notifications_admin_activees — si désactivé, ne fait rien.
    Dégradation gracieuse : si Brevo ou Config Service est indisponible, on logue et on continue.
    """
    if not config_client.get_notifications_admin_activees():
        logger.debug("Notifications admin désactivées — événement ignoré", extra={"evenement": evenement})
        return

    _SUJETS: dict[str, str] = {
        "CAMPAGNE_PLANIFIEE": "[SGFE] Campagne planifiée",
        "SUSPENSION": "[SGFE] Suspension d'abonné",
        "ECHEC_WHATSAPP": "[SGFE] Échec envoi WhatsApp",
    }
    sujet = _SUJETS.get(evenement, f"[SGFE] Événement : {evenement}")
    corps = f"Événement : {evenement}\nEntité : {entite_id or 'N/A'}\n\n{detail}"
    email_admin = config_client.get_email_admin_notifications()
    envoyer_email_admin(to_email=email_admin, subject=sujet, body=corps)


class TokenService:
    """Logique métier de gestion des tokens d'accès abonné."""

    def __init__(self) -> None:
        self._tokens = TokenAccesRepository()

    def get_or_create_token(self, abonne_id: str, facture_id: str) -> TokenAcces:
        """Retourne un token d'accès valide pour l'abonné, en créant un si besoin.

        L'espace abonné donne accès à tout l'historique de l'abonné : on
        réutilise donc son token actif non expiré le plus récent (peu importe
        la facture d'origine). S'il n'en existe pas, on en crée un avec la durée
        de validité configurée (clé `token_validite_jours`). `facture_id` ne sert
        qu'à renseigner la facture ayant déclenché la création.
        """
        existant = self._tokens.get_latest_valid_by_abonne(abonne_id)
        if existant:
            return existant
        validite_jours = config_client.get_token_validite_jours()
        return self._tokens.create(
            abonne_id=abonne_id,
            facture_id=facture_id,
            date_expiration=date.today() + timedelta(days=validite_jours),
        )

    def valider_token(self, token_str: str) -> TokenAcces:
        """Vérifie qu'un token est valide (actif et non expiré).

        Args:
            token_str: La valeur UUID du token partagé dans l'URL.

        Returns:
            Le TokenAcces correspondant.

        Raises:
            ObjectDoesNotExist: Si le token est introuvable.
            ValueError: Si le token est révoqué ou expiré.
        """
        token = self._tokens.get_by_token(token_str)

        if not token.is_active:
            raise ValueError("Ce token d'accès a été révoqué.")

        if token.date_expiration < date.today():
            raise ValueError("Ce token d'accès a expiré.")

        # Mise à jour de la dernière visite
        token.date_derniere_visite = timezone.now()
        self._tokens.save(token)

        return token

    def revoquer_token(self, token_id: str) -> None:
        """Révoque un token d'accès (is_active = False).

        Args:
            token_id: L'UUID primaire du TokenAcces.

        Raises:
            ObjectDoesNotExist: Si le token est introuvable.
        """
        token = self._tokens.get_by_id(token_id)
        token.is_active = False
        self._tokens.save(token)
        logger.info("Token révoqué", extra={"token_id": token_id})

    def revoquer_tous_tokens(self) -> int:
        """Révoque en masse tous les tokens d'accès abonné actifs.

        Returns:
            Le nombre de tokens qui étaient actifs et ont été révoqués.
        """
        count = self._tokens.revoquer_tous_actifs()
        logger.info("Révocation de masse des tokens d'accès", extra={"count": count})
        return count
