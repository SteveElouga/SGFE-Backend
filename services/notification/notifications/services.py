"""Logique métier du Notification Service.

EnvoiService : envoi et suivi des messages WhatsApp.
TokenService : gestion des tokens d'accès à l'espace abonné.
DiffusionService : messages libres envoyés à un ensemble d'abonnés.
"""

import logging
from datetime import date, timedelta

import grpc
from django.conf import settings
from django.core.exceptions import ValidationError
from django.utils import timezone

from notifications.brevo_client import envoyer_email_admin
from notifications.grpc_clients import (
    abonne_client,
    config_client,
    facturation_client,
    paiement_client,
)
from notifications.message_builder import (
    build_message_facture,
    build_message_recu,
    build_message_relance_1,
    build_message_relance_2,
    build_message_relance_3,
    build_message_relance_4,
    build_message_annulation_facture,
    build_message_annulation_paiement,
    build_message_retablissement,
)
from notifications.models import (
    MAX_TENTATIVES_AUTO,
    Diffusion,
    Envoi,
    StatutDiffusionEnvoi,
    StatutEnvoi,
    TokenAcces,
    TypeEnvoi,
)
from notifications.repositories import DiffusionRepository, EnvoiRepository, TokenAccesRepository
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
    # Étape 5 = annulation d'un versement. Elle sort de l'échelle des relances,
    # qui va de 1 à 4 et monte en fermeté ; 5 n'est pas « plus ferme que la
    # suspension », c'est un autre sujet. Le champ `etape` du proto est un
    # entier, ce qui évite de modifier le contrat pour un message de plus — mais
    # si une seconde notification hors relance s'ajoute, cette échelle méritera
    # d'être remplacée par un type explicite.
    5: TypeEnvoi.ANNULATION_PAIEMENT,
    # Étape 6 = annulation d'une facture jamais payée — la seconde notification
    # hors relance annoncée par le commentaire ci-dessus. Distincte de l'étape 5 :
    # là, un versement existait et devient un avoir ; ici, aucun argent n'a
    # changé de main, et le dire comme un versement annulé serait faux.
    6: TypeEnvoi.ANNULATION_FACTURE,
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


def _jours_de_retard(date_limite: str) -> int:
    """Jours écoulés depuis l'échéance. 0 si elle n'est pas encore passée.

    Le retard annoncé dans une relance doit être le VRAI retard, pas le délai que
    le cron était censé respecter. Les deux coïncident quand le cron passe le bon
    jour ; ils divergent dès qu'une dette est saisie avec une échéance passée, ou
    que le cron a manqué des jours.

    Une date illisible rend 0 : le message se rabat sur « aujourd'hui », ce qui
    est vague mais jamais faux dans un sens qui rassure à tort.
    """
    if not date_limite:
        return 0
    try:
        limite = date.fromisoformat(str(date_limite)[:10])
    except ValueError:
        return 0
    return max(0, (date.today() - limite).days)


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

    def _lien_espace_abonne(self, abonne_id: str, facture_id: str) -> str:
        """URL de l'espace abonné, en réutilisant/créant un token via TokenService.

        Chaque message envoyé à un abonné doit porter ce lien (le reçu et les
        relances 2 à 4 ne l'incluaient pas, contrairement au message de facture
        et à la relance 1) : l'abonné n'avait alors aucun moyen d'y accéder
        depuis ces messages-là.
        """
        token = TokenService().get_or_create_token(abonne_id=abonne_id, facture_id=facture_id)
        return f"{settings.FRONTEND_URL}/espace/{token.token}"

    def get_whatsapp_qr(self) -> tuple[bool, str, str, str, int]:
        """Retourne (ready, qr, number, phase, depuis_ms) pour l'affichage admin.

        `number` est le numéro du compte appairé (vide si non connecté). `phase`
        dit pourquoi la liaison n'est pas prête — « demarrage » et « rupture »
        appellent des messages opposés, et l'UI ne pouvait pas les distinguer.

        Dégradation gracieuse : si le service whatsapp-web.js est inaccessible,
        la phase est « rupture » — c'en est une, et la taire donnerait à l'écran
        la même apparence qu'un démarrage en cours, c'est-à-dire une attente
        que rien ne viendra clore.
        """
        try:
            return whatsapp_client.get_qr()
        except WhatsAppDeliveryError as exc:
            logger.warning("QR WhatsApp indisponible : %s", exc)
            return (False, "", "", "rupture", 0)

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

        # Le message doit annoncer le MÊME total que le PDF qu'il transporte.
        # Avant, il affichait `facture.montant` — la consommation du mois seule —
        # pendant que la pièce jointe additionnait la dette antérieure et
        # retranchait l'avoir. L'abonné paie ce qu'il lit dans WhatsApp, pas ce
        # qu'il y a dans le PDF : il payait donc le mauvais montant, et se
        # faisait relancer pour une différence dont personne ne l'avait informé.
        #
        # Les deux appels dégradent gracieusement (voir PaiementServiceClient) :
        # si Paiement est indisponible, le message part avec la consommation
        # seule plutôt que de ne pas partir.
        solde_ant, nb_fact_ant, plus_ancienne = paiement_client.get_dette_abonne(
            abonne_id=abonne_id, hors_facture_id=facture_id
        )
        avoir = paiement_client.get_avoir_impute(facture_id)

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
            solde_anterieur=solde_ant,
            nb_factures_anterieures=nb_fact_ant,
            plus_ancienne_echeance=_format_date_fr(plus_ancienne) if plus_ancienne else "",
            avoir_impute=avoir,
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

    def envoyer_recu(
        self,
        paiement_id: str,
        facture_id: str,
        abonne_id: str,
        montant: float,
        solde_restant: float,
    ) -> Envoi:
        """Envoie le reçu de paiement (PDF) à l'abonné après un versement.

        Confirmation WhatsApp avec le reçu en pièce jointe. Même dégradation
        gracieuse que envoyer_facture : un service amont injoignable donne un
        Envoi ECHEC (jamais de RpcError brute), et un reçu PDF indisponible
        (Facturation KO) n'empêche pas l'envoi du message de confirmation.
        """
        try:
            facture = facturation_client.get_facture(facture_id)
            abonne = abonne_client.get_abonne(abonne_id)
        except grpc.RpcError as exc:
            return self._echec_amont(facture_id, abonne_id, TypeEnvoi.RECU, exc)

        prenom_nom = f"{abonne.prenom} {abonne.nom.upper()}"
        telephone = abonne.telephone_whatsapp
        periode = _periode_from_date(facture.date_releve)

        message = build_message_recu(
            prenom_nom=prenom_nom,
            periode=periode,
            montant=montant,
            solde_restant=solde_restant,
            lien_espace=self._lien_espace_abonne(abonne_id, facture_id),
        )

        envoi = self._envois.create(
            facture_id=facture_id,
            abonne_id=abonne_id,
            type_envoi=TypeEnvoi.RECU,
            telephone=telephone,
            paiement_id=paiement_id,
        )

        # Reçu PDF depuis Facturation (dégradation gracieuse : si indisponible,
        # generer_recu_paiement_pdf renvoie (b"", "") et le message part seul).
        # `montant` et `solde_restant` sont ceux du VERSEMENT : ce que l'abonné a
        # tendu, et ce qu'il doit encore en tout. Les mêmes chiffres que le
        # message ci-dessus — sans quoi le reçu joint annoncerait autre chose que
        # le texte qui le transporte.
        pdf_bytes, pdf_filename = facturation_client.generer_recu_paiement_pdf(
            paiement_id,
            facture_id,
            montant_versement=montant,
            solde_restant_total=solde_restant,
        )

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

    def envoyer_relance(
        self,
        facture_id: str,
        abonne_id: str,
        etape: int,
        jours_avant_suspension: int = 0,
    ) -> Envoi:
        """Envoie le message correspondant à l'étape (0 à 6).

        Args:
            facture_id: Identifiant de la facture.
            abonne_id: Identifiant de l'abonné.
            etape: 0 = rétablissement, 1 = rappel doux, 2 = rappel ferme,
                   3 = avertissement, 4 = suspension, 5 = annulation d'un
                   versement, 6 = annulation d'une facture jamais payée.
            jours_avant_suspension: Étape 3 seulement — jours restants avant la
                coupure, lus dans Config par le cron. 0 = ne pas annoncer de
                délai, plutôt qu'en annoncer un faux.

        Raises:
            ValidationError: Si l'étape est hors de la plage [0, 6].
        """
        if etape not in _ETAPE_TO_TYPE:
            raise ValidationError(f"Étape de relance invalide : {etape}. Les étapes valides sont 0 à 6.")

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

        # ── Le reste dû, et non le montant de la facture ────────────────────
        #
        # `facture.montant` est la consommation du mois × le prix. Les relances
        # l'annonçaient tel quel : un abonné qui avait versé 8 000 sur 10 000
        # lisait « votre facture de 10 000 FCFA est impayée ». Son versement
        # n'était ni déduit ni même mentionné — et les factures PARTIELLE sont
        # bien relancées, la pause après acompte ne durant que quelques jours.
        #
        # `FactureResponse` n'expose ni `montant_paye` ni `solde_restant` : la
        # seule source de vérité est Paiement Service, et le client existait déjà
        # (il ne servait qu'à l'étape 5). `None` = illisible → le montant n'est
        # pas imprimé, plutôt qu'imprimé faux.
        reste_du = paiement_client.get_solde_restant(facture_id) if 1 <= etape <= 3 else None

        # ── Les AUTRES impayés de l'abonné ───────────────────────────────────
        #
        # Une relance ne parlait que de LA facture qui la déclenche. Un abonné
        # avec trois factures en retard reçoit trois relances distinctes,
        # chacune muette sur les deux autres — alors que le message de facture
        # initiale, lui, annonce déjà le solde antérieur avec `get_dette_abonne`.
        # Même source, même exclusion de la facture courante.
        autres_du, autres_nb, _ = (
            paiement_client.get_dette_abonne(abonne_id, hors_facture_id=facture_id) if 1 <= etape <= 3 else (0.0, 0, "")
        )

        # Le retard RÉEL, calculé depuis l'échéance de la facture. Les gabarits
        # écrivaient « depuis 3 jours » / « depuis 7 jours » en dur, en supposant
        # que le cron passe le jour exact. Une régularisation saisie avec sa
        # vraie échéance est immédiatement à plusieurs mois de retard.
        jours_retard = _jours_de_retard(facture.date_limite_paiement)

        if etape == 0:
            # Plus de montant : `facture.montant` n'est pas le versement, et
            # l'annoncer comme tel était faux à chaque paiement partiel.
            # Voir `build_message_retablissement`.
            message = build_message_retablissement(
                prenom_nom=prenom_nom,
                lien_espace=self._lien_espace_abonne(abonne_id, facture_id),
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
                montant=reste_du,
                lien_espace=lien,
                jours_retard=jours_retard,
                autres_impayes_total=autres_du,
                autres_impayes_nb=autres_nb,
            )
        elif etape == 2:
            message = build_message_relance_2(
                prenom_nom=prenom_nom,
                periode=periode,
                montant=reste_du,
                jours_retard=jours_retard,
                autres_impayes_total=autres_du,
                autres_impayes_nb=autres_nb,
                lien_espace=self._lien_espace_abonne(abonne_id, facture_id),
            )
        elif etape == 3:
            message = build_message_relance_3(
                prenom_nom=prenom_nom,
                montant=reste_du,
                jours_retard=jours_retard,
                # Transmis par le cron, qui a lu le délai dans Config. 0 = ne
                # rien annoncer, plutôt qu'annoncer un délai faux.
                jours_avant_suspension=jours_avant_suspension,
                autres_impayes_total=autres_du,
                autres_impayes_nb=autres_nb,
                lien_espace=self._lien_espace_abonne(abonne_id, facture_id),
            )
        elif etape == 4:
            try:
                infos = config_client.get_infos_societe()
                telephone_societe = infos.telephone
            except Exception:
                telephone_societe = ""
            # La SUSPENSION doit dire quoi payer pour être rétabli — et depuis
            # RS-005, c'est la dette TOTALE, pas le montant d'une facture.
            total_du, _, _ = paiement_client.get_dette_abonne(abonne_id)
            message = build_message_relance_4(
                prenom_nom=prenom_nom,
                montant=total_du if total_du > 0 else None,
                periode=periode,
                telephone_societe=telephone_societe,
                lien_espace=self._lien_espace_abonne(abonne_id, facture_id),
            )
        elif etape == 5:  # annulation d'un versement
            # Le solde restant vient du service paiement, seule source de vérité :
            # `facture.montant` est le montant du mois, pas ce qui reste dû après
            # d'éventuels autres versements.
            #
            # Le client rend `None` s'il est injoignable — et non zéro. Un
            # « reste à payer : 0 » serait un mensonge tranquille ; on se rabat
            # alors sur le montant de la facture, du bon ordre de grandeur et
            # jamais rassurant à tort.
            #
            # Le zéro LÉGITIME, lui, est désormais distingué de l'inconnu : une
            # facture restée couverte après l'annulation (avoir imputé, autre
            # versement) annonce bien qu'il n'y a rien à payer, là où l'ancien
            # `> 0` faisait ressortir le montant plein d'une facture soldée.
            solde_restant = paiement_client.get_solde_restant(facture_id)
            message = build_message_annulation_paiement(
                prenom_nom=prenom_nom,
                periode=periode,
                solde_restant=facture.montant if solde_restant is None else solde_restant,
                lien_espace=self._lien_espace_abonne(abonne_id, facture_id),
            )
        else:  # etape == 6 — annulation d'une facture jamais payée
            message = build_message_annulation_facture(
                prenom_nom=prenom_nom,
                periode=periode,
                lien_espace=self._lien_espace_abonne(abonne_id, facture_id),
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

        Renseigne systématiquement `dernier_message`/`avec_pdf`/`pdf_filename`
        AVANT la tentative — premier essai ou retry automatique
        (`retry_envois_echec_job`) : c'est ce texte, figé, qui sera rejoué à
        l'identique en cas d'échec, plutôt que de recalculer le message métier.
        """
        envoi.dernier_message = message
        envoi.avec_pdf = bool(pdf_bytes)
        envoi.pdf_filename = pdf_filename if pdf_bytes else ""
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

    def _regenerer_pdf_retry(self, envoi: Envoi) -> tuple[bytes, str]:
        """Régénère le PDF à joindre à un retry, via le client facturation
        existant — jamais stocké en base (voir `Envoi.avec_pdf`).

        Pour un reçu (`TypeEnvoi.RECU`), appelle `generer_recu_paiement_pdf`
        sans `montant_versement`/`solde_restant_total` : Facturation Service
        retrouve le montant réel du versement via `paiement_id` (source de
        vérité), ces deux paramètres ne servant qu'à l'imputation affichée —
        exactement le chemin déjà emprunté par une régénération manuelle
        depuis le back-office (voir `factures/services.py::generer_recu_pdf`).

        Dégrade comme au premier envoi : un PDF introuvable ne bloque pas le
        retry, le message texte part seul (les deux méthodes du client
        rendent déjà `(b"", "")` en cas d'échec).
        """
        if envoi.type_envoi == TypeEnvoi.RECU:
            return facturation_client.generer_recu_paiement_pdf(envoi.paiement_id, envoi.facture_id)
        return facturation_client.get_facture_pdf(envoi.facture_id)

    def retenter_echecs(self, taille_lot: int) -> list[Envoi]:
        """Retente un lot d'envois WhatsApp en ECHEC sous le plafond de
        tentatives automatiques (voir `MAX_TENTATIVES_AUTO`).

        Rejoue le dernier message tenté (`Envoi.dernier_message`) à
        l'identique, jamais recalculé — voir `_tenter_envoi`. Régénère le PDF
        au besoin (`Envoi.avec_pdf`), jamais stocké en base.

        Le plafond est un cap dur : le filtre du repository
        (`tentatives < MAX_TENTATIVES_AUTO`) garantit qu'un envoi qui l'a déjà
        atteint n'est plus jamais sélectionné, donc plus jamais retenté ici.
        Quand une tentative fait franchir ce seuil pour la première fois (elle
        échoue encore et `tentatives` atteint le plafond), on logue un message
        distinct d'abandon définitif et on notifie les admins via un
        événement dédié (`ABANDON_RETRY_WHATSAPP`) — jamais un doublon de la
        notification `ECHEC_WHATSAPP` déjà envoyée par `_tenter_envoi`.

        Appelée uniquement par `retry_envois_echec_job` (schedulers.py) —
        jamais par un RPC : le rythme des retries est celui du job de fond.
        """
        lot = self._envois.list_echecs_a_retenter(taille_lot)
        for envoi in lot:
            pdf_bytes, pdf_filename_regenere = (b"", "")
            if envoi.avec_pdf:
                pdf_bytes, pdf_filename_regenere = self._regenerer_pdf_retry(envoi)
            self._tenter_envoi(
                envoi,
                envoi.telephone,
                envoi.dernier_message,
                pdf_bytes=pdf_bytes,
                pdf_filename=envoi.pdf_filename or pdf_filename_regenere,
            )
            if envoi.statut == StatutEnvoi.ECHEC and envoi.tentatives >= MAX_TENTATIVES_AUTO:
                logger.warning(
                    "Abandon définitif après %d tentatives automatiques",
                    MAX_TENTATIVES_AUTO,
                    extra={
                        "envoi_id": str(envoi.id),
                        "facture_id": envoi.facture_id,
                        "type_envoi": envoi.type_envoi,
                    },
                )
                notifier_admins(
                    evenement="ABANDON_RETRY_WHATSAPP",
                    detail=(
                        f"Abandon définitif de l'envoi WhatsApp {envoi.id} (facture {envoi.facture_id}) "
                        f"après {MAX_TENTATIVES_AUTO} tentatives automatiques : {envoi.erreur}"
                    ),
                    entite_id=envoi.facture_id,
                )
        return lot


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
        "ABANDON_RETRY_WHATSAPP": "[SGFE] Abandon définitif d'un envoi WhatsApp",
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


class DiffusionService:
    """Logique métier des diffusions — message libre vers un ensemble d'abonnés.

    L'envoi lui-même n'a pas lieu ici : cette classe ne fait que créer la
    diffusion et ses lignes ``EN_ATTENTE``. C'est `traiter_lot_en_attente`
    (appelée par le job de fond, `schedulers.py`) qui les envoie réellement,
    quelques-unes à la fois — jamais toutes d'un coup, pour ne pas ressembler
    à du spam sur le compte WhatsApp Web partagé par tout le système.
    """

    def __init__(self) -> None:
        self._diffusions = DiffusionRepository()

    def creer_diffusion(self, message: str, abonne_ids: list[str], created_by: str = "") -> Diffusion:
        """Crée la diffusion et une ligne d'envoi par abonné dont le téléphone
        a pu être résolu.

        Un abonné introuvable ou un Abonné Service injoignable pour CET
        abonné précis ne bloque pas les autres : dégradation par abonné, pas
        par diffusion entière — même esprit que `_echec_amont` pour un envoi
        individuel, mais ici on omet la ligne plutôt que de créer un envoi
        qu'on sait déjà voué à l'échec.
        """
        resolus: list[tuple[str, str]] = []
        for abonne_id in abonne_ids:
            try:
                abonne = abonne_client.get_abonne(abonne_id)
            except grpc.RpcError as exc:
                logger.warning(
                    "Abonné introuvable pour la diffusion — ligne omise",
                    extra={"abonne_id": abonne_id, "erreur": str(exc)},
                )
                continue
            resolus.append((abonne_id, abonne.telephone_whatsapp))

        return self._diffusions.create(message=message, created_by=created_by, abonnes=resolus)

    def get_diffusion(self, diffusion_id: str) -> Diffusion:
        """Récupère une diffusion par son UUID. Lève ObjectDoesNotExist si absente."""
        return self._diffusions.get_by_id(diffusion_id)

    def list_diffusions(self) -> list[Diffusion]:
        """Liste toutes les diffusions, la plus récente d'abord."""
        return self._diffusions.list_all()

    def compter(self, diffusion: Diffusion) -> tuple[int, int, int]:
        """(nb_total, nb_envoyes, nb_echecs) d'une diffusion."""
        return self._diffusions.compter(diffusion)

    def traiter_lot_en_attente(self, taille_lot: int) -> list[str]:
        """Envoie un lot de lignes ``EN_ATTENTE`` et referme les diffusions
        complètes. Retourne les id des diffusions dont l'état (progression ou
        passage à TERMINEE) vient de changer — pour que l'appelant publie
        l'événement Redis correspondant.

        Appelée par le job de fond (`schedulers.py`), jamais directement par
        un RPC : un client gRPC n'a pas à décider du rythme d'envoi.
        """
        lot = self._diffusions.prochains_en_attente(taille_lot)
        diffusion_ids_touchees = set()

        for envoi in lot:
            diffusion_ids_touchees.add(str(envoi.diffusion_id))
            try:
                whatsapp_client.send(envoi.telephone, envoi.diffusion.message)
                envoi.statut = StatutDiffusionEnvoi.ENVOYE
                envoi.date_envoi = timezone.now()
            except WhatsAppDeliveryError as exc:
                envoi.statut = StatutDiffusionEnvoi.ECHEC
                envoi.erreur = str(exc)
                logger.warning(
                    "Échec envoi diffusion",
                    extra={"diffusion_id": str(envoi.diffusion_id), "abonne_id": envoi.abonne_id, "erreur": str(exc)},
                )
            self._diffusions.save_envoi(envoi)

        diffusion_ids_touchees.update(self._diffusions.terminer_si_completes())
        return list(diffusion_ids_touchees)
