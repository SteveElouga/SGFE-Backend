"""Types Strawberry pour le Notification Service."""

import strawberry


@strawberry.type
class Envoi:
    envoi_id: str
    abonne_id: str
    facture_id: str
    type_envoi: str  # FACTURE | RELANCE_1..4 | AVERTISSEMENT | SUSPENSION | RETABLISSEMENT
    statut: str  # EN_ATTENTE | ENVOYE | ECHEC
    date_envoi: str
    message_id: str  # identifiant technique du message (ex-telnyx_message_id)
    raison_echec: str  # motif d'échec (vide si succès)
    # Conservés pour rétro-compatibilité (mêmes valeurs que message_id / raison_echec).
    telnyx_message_id: str
    erreur: str


def envoi_from_grpc(r) -> Envoi:
    return Envoi(
        envoi_id=r.envoi_id,
        abonne_id=getattr(r, "abonne_id", ""),
        facture_id=r.facture_id,
        type_envoi=getattr(r, "type_envoi", ""),
        statut=r.statut,
        date_envoi=r.date_envoi,
        message_id=r.telnyx_message_id,
        raison_echec=r.erreur,
        telnyx_message_id=r.telnyx_message_id,
        erreur=r.erreur,
    )


@strawberry.type
class WhatsAppQr:
    """Statut de connexion WhatsApp + QR de liaison + numéro appairé, pour l'UI admin.

    `qr` est une data-URL PNG à afficher directement (`<img src>`), vide si
    `ready` est vrai (déjà connecté) ou si le service est en cours d'init /
    indisponible. `number` est le numéro du compte WhatsApp appairé (« N° du
    compte dédié »), présent uniquement quand `ready` est vrai.
    """

    ready: bool
    qr: str
    number: str


def whatsapp_qr_from_grpc(r) -> WhatsAppQr:
    return WhatsAppQr(ready=r.ready, qr=r.qr, number=r.number)


@strawberry.type
class TestEnvoiResult:
    """Résultat d'un test d'envoi WhatsApp : succès + motif en cas d'échec."""

    success: bool
    message: str
