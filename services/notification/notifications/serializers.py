"""Sérialiseurs — conversion des modèles Django en messages protobuf."""

import sys
from pathlib import Path

from django.conf import settings

from notifications.models import Diffusion, Envoi, TokenAcces

# Import tardif des stubs générés
_proto_path = str(Path(settings.BASE_DIR) / "proto")
if _proto_path not in sys.path:
    sys.path.insert(0, _proto_path)

import notification_service_pb2 as pb  # type: ignore[import]  # noqa: E402


def envoi_to_proto(envoi: Envoi) -> pb.EnvoiResponse:
    """Convertit un modèle Envoi en message protobuf EnvoiResponse.

    Args:
        envoi: Instance du modèle Django Envoi.

    Returns:
        EnvoiResponse protobuf prêt à être renvoyé par le serveur gRPC.
    """
    date_envoi_str = ""
    if envoi.date_envoi:
        date_envoi_str = envoi.date_envoi.isoformat()

    return pb.EnvoiResponse(
        envoi_id=str(envoi.id),
        facture_id=envoi.facture_id,
        statut=envoi.statut,
        date_envoi=date_envoi_str,
        telnyx_message_id=envoi.telnyx_message_id or "",
        erreur=envoi.erreur or "",
        type_envoi=envoi.type_envoi or "",
        abonne_id=envoi.abonne_id or "",
        paiement_id=envoi.paiement_id or "",
    )


def diffusion_to_proto(diffusion: Diffusion, compteurs: tuple[int, int, int]) -> pb.DiffusionResponse:
    """Convertit une Diffusion en message protobuf DiffusionResponse.

    `compteurs` (nb_total, nb_envoyes, nb_echecs) est calculé par
    l'appelant via `DiffusionRepository.compter` — jamais stocké sur le
    modèle, pour ne jamais afficher un chiffre qui a dérivé de l'état réel.
    """
    nb_total, nb_envoyes, nb_echecs = compteurs
    return pb.DiffusionResponse(
        diffusion_id=str(diffusion.id),
        message=diffusion.message,
        statut=diffusion.statut,
        nb_total=nb_total,
        nb_envoyes=nb_envoyes,
        nb_echecs=nb_echecs,
        created_by=diffusion.created_by or "",
        created_at=diffusion.created_at.isoformat() if diffusion.created_at else "",
    )


def token_to_valider_response(token: TokenAcces) -> pb.ValiderTokenResponse:
    """Convertit un TokenAcces valide en ValiderTokenResponse protobuf.

    Args:
        token: Instance du modèle Django TokenAcces validé.

    Returns:
        ValiderTokenResponse avec is_valid=True et les informations du token.
    """
    return pb.ValiderTokenResponse(
        is_valid=True,
        abonne_id=token.abonne_id,
        date_expiration=str(token.date_expiration),
    )
