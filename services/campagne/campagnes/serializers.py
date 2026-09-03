"""Sérialisation entre les modèles Django et les messages protobuf."""

import sys
from datetime import date, datetime
from pathlib import Path
from typing import Union

from django.conf import settings

sys.path.insert(0, str(Path(settings.BASE_DIR) / "proto"))

import campagne_service_pb2 as pb

from campagnes.dtos import AgentAffecteDict
from campagnes.models import Campagne, Releve, ReleveAudit


def _to_iso(value: Union[date, datetime, str, None]) -> str:
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
        created_by=campagne.created_by,
    )


def audit_to_proto(audit: ReleveAudit) -> pb.ReleveAudit:
    """Convertit une entrée d'audit en message protobuf ReleveAudit."""
    return pb.ReleveAudit(
        action=audit.action,
        auteur_id=audit.auteur_id,
        auteur_username=audit.auteur_username,
        auteur_role=audit.auteur_role,
        # Frontière gRPC sortante : le proto transporte un double, la donnée
        # interne est un Decimal (voir campagnes/models.py).
        ancien_index=float(audit.ancien_index) if audit.ancien_index is not None else 0.0,
        nouvel_index=float(audit.nouvel_index) if audit.nouvel_index is not None else 0.0,
        horodatage=_to_iso(audit.horodatage),
    )


def releve_to_proto(releve: Releve) -> pb.ReleveResponse:
    """Convertit un objet Releve en message protobuf ReleveResponse.

    Le journal d'audit est chargé via ``releve.audits`` : préférer un
    ``prefetch_related("audits")`` en amont pour les listes (évite le N+1).
    """
    return pb.ReleveResponse(
        releve_id=str(releve.id),
        abonne_id=releve.abonne_id,
        # Frontière gRPC sortante : le proto transporte un double, la donnée
        # interne est un Decimal (voir campagnes/models.py).
        ancien_index=float(releve.ancien_index),
        nouveau_index=float(releve.nouveau_index) if releve.nouveau_index is not None else 0.0,
        consommation=float(releve.consommation) if releve.consommation is not None else 0.0,
        date_releve=_to_iso(releve.date_releve),
        observation=releve.observation,
        statut=releve.statut,
        agent_id=releve.agent_id,
        quartier=releve.quartier,
        camp=releve.camp if releve.camp is not None else 0,
        # Un relevé non persisté (instance en mémoire) n'a pas encore d'audit :
        # on évite l'accès à la relation, qui déclencherait une requête inutile.
        # (la PK ne suffit pas comme test : elle a un default=uuid4 dès l'init.)
        audit=[audit_to_proto(a) for a in releve.audits.all()] if not releve._state.adding else [],
    )


def agent_affecte_to_proto(agent: AgentAffecteDict) -> pb.AgentAffecte:
    """Convertit un dict d'agent affecté (voir CampagneService.list_agents_campagne)
    en message protobuf AgentAffecte."""
    return pb.AgentAffecte(
        agent_id=agent["agent_id"],
        zones=[
            pb.ZoneAffectee(quartier=z["quartier"], camp=z["camp"], nb_releves=z["nb_releves"]) for z in agent["zones"]
        ],
        nb_releves=agent["nb_releves"],
        derniere_activite=_to_iso(agent["derniere_activite"]),
    )
