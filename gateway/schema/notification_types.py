"""Types Strawberry pour le Notification Service."""

import strawberry


@strawberry.type
class Envoi:
    envoi_id: str
    facture_id: str
    statut: str
    date_envoi: str
    telnyx_message_id: str
    erreur: str


def envoi_from_grpc(r) -> Envoi:
    return Envoi(
        envoi_id=r.envoi_id,
        facture_id=r.facture_id,
        statut=r.statut,
        date_envoi=r.date_envoi,
        telnyx_message_id=r.telnyx_message_id,
        erreur=r.erreur,
    )


@strawberry.type
class WhatsAppQr:
    """Statut de connexion WhatsApp + QR de liaison, pour l'UI admin.

    `qr` est une data-URL PNG à afficher directement (`<img src>`), vide si
    `ready` est vrai (déjà connecté) ou si le service est en cours d'init /
    indisponible.
    """

    ready: bool
    qr: str


def whatsapp_qr_from_grpc(r) -> WhatsAppQr:
    return WhatsAppQr(ready=r.ready, qr=r.qr)
