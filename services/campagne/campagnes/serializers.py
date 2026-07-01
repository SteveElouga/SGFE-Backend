"""Sérialisation entre les modèles Django et les messages protobuf."""

import sys
from pathlib import Path

from django.conf import settings

sys.path.insert(0, str(Path(settings.BASE_DIR) / "proto"))

import campagne_service_pb2 as pb

from campagnes.models import Campagne, Releve


def campagne_to_proto(campagne: Campagne) -> pb.CampagneResponse:
    """Convertit un objet Campagne en message protobuf CampagneResponse."""
    return pb.CampagneResponse(
        campagne_id=str(campagne.id),
        nom=campagne.nom,
        periode_mois=campagne.periode_mois,
        periode_annee=campagne.periode_annee,
        statut=campagne.statut,
        date_planifiee=campagne.date_planifiee.isoformat() if campagne.date_planifiee else "",
        date_creation=campagne.date_creation.isoformat() if campagne.date_creation else "",
        date_cloture=campagne.date_cloture.isoformat() if campagne.date_cloture else "",
        numero_mobile_money=campagne.numero_mobile_money,
    )


def releve_to_proto(releve: Releve) -> pb.ReleveResponse:
    """Convertit un objet Releve en message protobuf ReleveResponse."""
    return pb.ReleveResponse(
        releve_id=str(releve.id),
        abonne_id=releve.abonne_id,
        ancien_index=releve.ancien_index,
        nouveau_index=releve.nouveau_index if releve.nouveau_index is not None else 0.0,
        consommation=releve.consommation if releve.consommation is not None else 0.0,
        date_releve=releve.date_releve.isoformat() if releve.date_releve else "",
        observation=releve.observation,
        statut=releve.statut,
    )
