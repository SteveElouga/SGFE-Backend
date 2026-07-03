"""Sérialisation entre les modèles Django et les messages protobuf."""

import sys
from pathlib import Path

from django.conf import settings

sys.path.insert(0, str(Path(settings.BASE_DIR) / "proto"))

import campagne_service_pb2 as pb

from campagnes.models import Campagne, Releve


def _to_iso(value) -> str:
    """Convertit date/datetime/str en format ISO, ou '' si None/vide.

    Après Campagne.objects.create(), Django peut retourner la valeur d'origine
    (str) sans la convertir en date — cette fonction gère les deux cas.
    """
    if not value:
        return ""
    if isinstance(value, str):
        return value
    return value.isoformat()


def campagne_to_proto(campagne: Campagne) -> pb.CampagneResponse:
    """Convertit un objet Campagne en message protobuf CampagneResponse."""
    return pb.CampagneResponse(
        campagne_id=str(campagne.id),
        nom=campagne.nom,
        periode_mois=campagne.periode_mois,
        periode_annee=campagne.periode_annee,
        statut=campagne.statut,
        date_planifiee=_to_iso(campagne.date_planifiee),
        date_creation=_to_iso(campagne.date_creation),
        date_cloture=_to_iso(campagne.date_cloture),
        numero_mobile_money=campagne.numero_mobile_money,
        generer_factures_auto=campagne.generer_factures_auto,
        envoyer_whatsapp_auto=campagne.envoyer_whatsapp_auto,
    )


def releve_to_proto(releve: Releve) -> pb.ReleveResponse:
    """Convertit un objet Releve en message protobuf ReleveResponse."""
    return pb.ReleveResponse(
        releve_id=str(releve.id),
        abonne_id=releve.abonne_id,
        ancien_index=releve.ancien_index,
        nouveau_index=releve.nouveau_index if releve.nouveau_index is not None else 0.0,
        consommation=releve.consommation if releve.consommation is not None else 0.0,
        date_releve=_to_iso(releve.date_releve),
        observation=releve.observation,
        statut=releve.statut,
    )
