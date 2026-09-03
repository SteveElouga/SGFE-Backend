"""Mutations GraphQL du Notification Service."""

import logging

import grpc
import strawberry
import strawberry.types

from .context import require_auth, require_role
from .grpc_clients import config_client, notification_client, paiement_client
from .notification_types import Envoi, TestEnvoiResult, envoi_from_grpc

logger = logging.getLogger(__name__)

# Inverse de `_ETAPE_TO_TYPE` du service de notification : un envoi de relance
# porte son type, et c'est l'étape qui commande le message à reconstruire.
#
# La table est recopiée ici plutôt que partagée parce que les deux processus ne
# partagent aucun code — et elle est verrouillée par un test qui la confronte à
# celle du service. Sans lui, une étape ajoutée d'un côté serait renvoyée comme
# une facture de l'autre, silencieusement.
_TYPE_TO_ETAPE: dict[str, int] = {
    "RETABLISSEMENT": 0,
    "RELANCE_1": 1,
    "RELANCE_2": 2,
    "AVERTISSEMENT": 3,
    "SUSPENSION": 4,
    "ANNULATION_PAIEMENT": 5,
}

# Défaut du service de configuration (`impaye_delai_suspension`). Recopié pour
# que le renvoi reste possible quand Config est injoignable.
_DELAI_SUSPENSION_DEFAUT = 10


def _delai_suspension() -> int:
    """Jours avant suspension, pour le message d'avertissement (étape 3).

    Le cron des impayés transmet cette valeur ; un renvoi manuel doit la lire
    lui-même, sinon l'avertissement renvoyé n'annonce plus aucun délai là où
    l'original en annonçait un.
    """
    try:
        return int(config_client.get_config("impaye_delai_suspension").valeur)
    except (grpc.RpcError, ValueError, AttributeError):
        # Un délai indisponible ne doit pas empêcher le renvoi : le message
        # retombe sur le défaut du service de configuration, qui est la valeur
        # qu'une installation non modifiée utilise de toute façon.
        return _DELAI_SUSPENSION_DEFAUT


def _envoyer_recu_paiement(paiement_id: str, facture_id: str, abonne_id: str):  # type: ignore[no-untyped-def]
    """Envoie le reçu d'un versement identifié directement par son id, avec
    les chiffres du jour.

    Cœur partagé par `_renvoyer_recu` (renvoi depuis le journal des envois) et
    `envoyer_recu_paiement` (envoi direct depuis l'écran du versement) — les
    deux chemins que peut emprunter le même geste, selon que l'envoi d'origine
    porte ou non l'identifiant de son versement.

    Un reçu dont le versement a été annulé depuis n'est pas envoyé : le
    document affirmerait un encaissement qui n'existe plus.
    """
    paiements = paiement_client.list_paiements(facture_id=facture_id).paiements
    paiement = next((p for p in paiements if p.paiement_id == paiement_id), None)
    if paiement is None:
        raise ValueError("Le versement de ce reçu n'existe plus.")
    if paiement.annule:
        raise ValueError("Ce versement a été annulé : son reçu ne vaut plus rien et n'est pas renvoyé.")

    # Le solde du jour, non celui de l'époque : le PDF joint est régénéré et lit
    # la même source. Deux chiffres différents sur un même message, c'est le
    # défaut qui a déjà été corrigé sur la génération des factures.
    #
    # `GetDetteAbonne`, pas `GetSolde` : ce dernier ne rend que le reste dû sur
    # CETTE facture. Un abonné qui vient de la solder intégralement, mais doit
    # encore sur une AUTRE, se voyait donc dire « plus rien n'est dû » — cette
    # dette réelle n'apparaissant nulle part dans son propre solde de facture.
    try:
        solde_restant = paiement_client.get_dette_abonne(abonne_id).total_du
    except grpc.RpcError:
        logger.warning("GetDetteAbonne injoignable à l'envoi d'un reçu", extra={"paiement_id": paiement_id})
        raise ValueError("Le solde de cet abonné est indisponible : le reçu n'est pas envoyé.") from None

    return notification_client.envoyer_recu(
        paiement_id=paiement_id,
        facture_id=facture_id,
        abonne_id=abonne_id,
        montant=paiement.montant,
        solde_restant=solde_restant,
    )


def _renvoyer_recu(envoi):  # type: ignore[no-untyped-def]
    """Renvoie le reçu d'un versement, avec les chiffres du jour.

    Le versement est retrouvé par son identifiant, désormais porté par l'envoi.
    Il ne l'était pas : rien ne disait de quel versement un reçu était le reçu,
    et c'est ce qui rendait le renvoi impossible autrement qu'en devinant.
    """
    if not envoi.paiement_id:
        # Reçu émis avant que l'envoi ne garde son versement : ce chemin ne
        # peut pas retrouver le versement. `envoyer_recu_paiement` le peut,
        # lui, puisqu'il part du versement — c'est le chemin que l'écran du
        # versement emprunte désormais.
        raise ValueError(
            "Ce reçu date d'avant l'enregistrement du versement dans le journal "
            "des envois : il ne peut pas être renvoyé. Renvoyez-le depuis l'écran "
            "du versement."
        )
    return _envoyer_recu_paiement(envoi.paiement_id, envoi.facture_id, envoi.abonne_id)


@strawberry.type
class NotificationMutations:
    @strawberry.mutation
    def envoyer_facture_whatsapp(self, info: strawberry.types.Info, facture_id: str, abonne_id: str) -> Envoi:
        """Envoie la facture par WhatsApp à l'abonné — ADMIN, COMPTABLE."""
        require_auth(info)
        require_role(info, "ADMIN", "COMPTABLE")
        return envoi_from_grpc(notification_client.envoyer_facture(facture_id=facture_id, abonne_id=abonne_id))

    @strawberry.mutation
    def renvoyer_facture_whatsapp(self, info: strawberry.types.Info, facture_id: str) -> Envoi:
        """Renvoie la facture par WhatsApp (déjà générée) — ADMIN, COMPTABLE."""
        require_auth(info)
        require_role(info, "ADMIN", "COMPTABLE")
        return envoi_from_grpc(notification_client.renvoyer_facture(facture_id=facture_id))

    @strawberry.mutation
    def envoyer_recu_paiement(
        self, info: strawberry.types.Info, paiement_id: str, facture_id: str, abonne_id: str
    ) -> Envoi:
        """Envoie le reçu d'un versement directement depuis l'écran du versement
        — ADMIN, COMPTABLE.

        `renvoyer_envoi` retrouve le versement d'un reçu par l'identifiant que
        l'envoi porte — absent des envois faits avant que ce lien n'existe, ce
        qu'il dit alors plutôt que de renvoyer autre chose à sa place. Cette
        mutation part du versement lui-même : elle marche toujours, y compris
        pour ces envois-là.
        """
        require_auth(info)
        require_role(info, "ADMIN", "COMPTABLE")
        return envoi_from_grpc(_envoyer_recu_paiement(paiement_id, facture_id, abonne_id))

    @strawberry.mutation
    def renvoyer_envoi(self, info: strawberry.types.Info, envoi_id: str) -> Envoi:
        """Renvoie **le même message** qu'un envoi identifié par son id — ADMIN, COMPTABLE.

        Le bouton « Renvoyer » de l'écran de suivi des envois passe par ici, et
        il s'affiche sur chaque ligne quel que soit son type. Cette fonction
        appelait `renvoyer_facture` dans tous les cas : renvoyer un **reçu**
        envoyait une facture à l'abonné, renvoyer un **avertissement** aussi. Le
        seul type pour lequel le bouton faisait ce qu'il annonçait était
        `FACTURE`.

        Chaque type reprend donc son propre chemin d'envoi. Les montants sont
        relus au moment du renvoi, jamais rejoués depuis l'envoi d'origine : le
        montant d'un versement est fixe, mais la dette restante ne l'est pas, et
        un reçu renvoyé six semaines plus tard doit annoncer le solde du jour —
        sinon il contredit le PDF joint, qui est régénéré, et tous les autres
        écrans.
        """
        require_auth(info)
        require_role(info, "ADMIN", "COMPTABLE")
        envoi = notification_client.get_envoi(envoi_id)
        type_envoi = envoi.type_envoi or "FACTURE"

        if type_envoi == "RECU":
            return envoi_from_grpc(_renvoyer_recu(envoi))

        etape = _TYPE_TO_ETAPE.get(type_envoi)
        if etape is not None:
            return envoi_from_grpc(
                notification_client.envoyer_relance(
                    facture_id=envoi.facture_id,
                    abonne_id=envoi.abonne_id,
                    etape=etape,
                    jours_avant_suspension=_delai_suspension(),
                )
            )

        # `FACTURE`, et tout type qu'une version future ajouterait sans passer
        # ici : le renvoi de facture reste le comportement par défaut, mais ce
        # n'est plus le comportement unique.
        return envoi_from_grpc(notification_client.renvoyer_facture(facture_id=envoi.facture_id))

    @strawberry.mutation
    def revoquer_token_abonne(self, info: strawberry.types.Info, token_id: str) -> bool:
        """Révoque un token d'accès abonné — ADMIN."""
        require_auth(info)
        require_role(info, "ADMIN")
        response = notification_client.revoquer_token(token_id=token_id)
        return response.success

    @strawberry.mutation
    def revoquer_tous_tokens_abonnes(self, info: strawberry.types.Info) -> int:
        """Révoque tous les tokens d'accès abonné actifs — ADMIN.

        Retourne le nombre de tokens révoqués.
        """
        require_auth(info)
        require_role(info, "ADMIN")
        return notification_client.revoquer_tous_tokens().count

    @strawberry.mutation
    def tester_envoi_whatsapp(self, info: strawberry.types.Info, phone_number: str) -> TestEnvoiResult:
        """Envoie un message de test WhatsApp au numéro fourni — ADMIN.

        Retourne `success=false` avec le motif exact si l'envoi échoue
        (WhatsApp non connecté, numéro invalide, service injoignable).
        """
        require_auth(info)
        require_role(info, "ADMIN")
        response = notification_client.tester_envoi(phone_number=phone_number)
        return TestEnvoiResult(success=response.success, message=response.message)
