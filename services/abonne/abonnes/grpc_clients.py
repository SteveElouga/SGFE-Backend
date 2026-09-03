"""Clients gRPC vers les services externes consommés par Abonné Service.

Jusqu'ici, Abonné Service n'appelait aucun autre service (voir CLAUDE.md) —
il n'exposait que des données qui lui sont propres. Ce module est son premier
client gRPC sortant, introduit uniquement pour l'export RGPD (droit à la
portabilité, voir `abonnes/export.py`) : relevés, factures, paiements et
envois WhatsApp d'un abonné vivent dans les bases des autres services,
jamais répliqués ici.

Chaque méthode laisse `grpc.RpcError` remonter tel quel plutôt que de
dégrader gracieusement en interne (contrairement à d'autres `grpc_clients.py`
du dépôt) : c'est `abonnes/export.py` qui décide, section par section, de
transformer un échec en `"disponible": false` — pas ce module.
"""

from __future__ import annotations

import sys
from pathlib import Path
from typing import Any

from django.conf import settings

from abonnes.grpc_auth import canal_authentifie


def _ensure_proto_in_syspath() -> None:
    proto_path = str(Path(settings.BASE_DIR) / "proto")
    if proto_path not in sys.path:
        sys.path.insert(0, proto_path)


class CampagneServiceClient:
    """Client gRPC vers Campagne Service (port 50053) — relevés d'un abonné.

    Pas de RPC dédié « relevés d'un abonné, toutes campagnes confondues » côté
    Campagne Service (`ListReleves` ne filtre que par campagne) : ce client
    liste les campagnes puis filtre localement par `abonne_id`. Volume
    assumé : le nombre de campagnes reste de l'ordre de la dizaine par an,
    et cette méthode ne sert que l'export RGPD (pas un chemin chaud).
    """

    def __init__(self) -> None:
        address = f"{settings.CAMPAGNE_GRPC_HOST}:{settings.CAMPAGNE_GRPC_PORT}"
        self._channel = canal_authentifie(address, settings.INTERNAL_GRPC_KEY)
        _ensure_proto_in_syspath()

        import campagne_service_pb2 as pb
        import campagne_service_pb2_grpc as pb_grpc

        self._stub = pb_grpc.CampagneServiceStub(self._channel)
        self._pb = pb

    def list_releves_abonne(self, abonne_id: str) -> list[dict[str, Any]]:
        """Relevés de l'abonné, toutes campagnes confondues.

        Raises:
            grpc.RpcError: si Campagne Service est injoignable — laissé
                remonter à l'appelant (voir docstring du module).
        """
        campagnes = self._stub.ListCampagnes(self._pb.ListCampagnesRequest()).campagnes
        releves: list[dict[str, Any]] = []
        for campagne in campagnes:
            reponse = self._stub.ListReleves(self._pb.CampagneIdRequest(campagne_id=campagne.campagne_id))
            releves.extend(
                {
                    "campagne_id": campagne.campagne_id,
                    "campagne_nom": campagne.nom,
                    "releve_id": r.releve_id,
                    "ancien_index": r.ancien_index,
                    "nouveau_index": r.nouveau_index,
                    "consommation": r.consommation,
                    "date_releve": r.date_releve,
                    "statut": r.statut,
                    "observation": r.observation,
                }
                for r in reponse.releves
                if r.abonne_id == abonne_id
            )
        return releves


class FacturationServiceClient:
    """Client gRPC vers Facturation Service (port 50054) — factures d'un abonné."""

    def __init__(self) -> None:
        address = f"{settings.FACTURATION_GRPC_HOST}:{settings.FACTURATION_GRPC_PORT}"
        self._channel = canal_authentifie(address, settings.INTERNAL_GRPC_KEY)
        _ensure_proto_in_syspath()

        import facturation_service_pb2 as pb
        import facturation_service_pb2_grpc as pb_grpc

        self._stub = pb_grpc.FacturationServiceStub(self._channel)
        self._pb = pb

    def list_factures_abonne(self, abonne_id: str) -> list[dict[str, Any]]:
        """Toutes les factures de l'abonné (CONSOMMATION et REGULARISATION).

        Raises:
            grpc.RpcError: si Facturation Service est injoignable.
        """
        reponse = self._stub.ListFactures(self._pb.ListFacturesRequest(abonne_id=abonne_id))
        return [
            {
                "facture_id": f.facture_id,
                "numero_facture": f.numero_facture,
                "campagne_id": f.campagne_id,
                "nature": f.nature,
                "montant": f.montant,
                "statut": f.statut,
                "date_generation": f.date_generation,
                "date_limite_paiement": f.date_limite_paiement,
                "motif": f.motif,
                "motif_annulation": f.motif_annulation,
                "date_annulation": f.date_annulation,
            }
            for f in reponse.factures
        ]


class PaiementServiceClient:
    """Client gRPC vers Paiement Service (port 50055) — paiements d'un abonné."""

    def __init__(self) -> None:
        address = f"{settings.PAIEMENT_GRPC_HOST}:{settings.PAIEMENT_GRPC_PORT}"
        self._channel = canal_authentifie(address, settings.INTERNAL_GRPC_KEY)
        _ensure_proto_in_syspath()

        import paiement_service_pb2 as pb
        import paiement_service_pb2_grpc as pb_grpc

        self._stub = pb_grpc.PaiementServiceStub(self._channel)
        self._pb = pb

    def list_paiements_abonne(self, abonne_id: str) -> list[dict[str, Any]]:
        """Tous les versements de l'abonné, y compris annulés (traçabilité).

        Raises:
            grpc.RpcError: si Paiement Service est injoignable.
        """
        reponse = self._stub.ListPaiements(self._pb.ListPaiementsRequest(abonne_id=abonne_id))
        return [
            {
                "paiement_id": p.paiement_id,
                "facture_id": p.facture_id,
                "montant": p.montant,
                "date_paiement": p.date_paiement,
                "mode_paiement": p.mode_paiement,
                "reference_transaction": p.reference_transaction,
                "annule": p.annule,
                "annule_le": p.annule_le,
            }
            for p in reponse.paiements
        ]


class NotificationServiceClient:
    """Client gRPC vers Notification Service (port 50056) — envois WhatsApp ciblant l'abonné.

    Ne couvre que `Envoi` (facture, relance, reçu, suspension...), tracé un
    par un et par abonné (`ListEnvois(abonne_id=...)`). Les diffusions
    (messages libres à un ensemble d'abonnés, `Diffusion`/`DiffusionEnvoi`)
    ne sont PAS incluses : aucun RPC de `notification_service.proto` ne liste
    les abonnés visés par une diffusion donnée (`ListDiffusions` ne renvoie
    que des compteurs agrégés) — voir `abonnes/export.py` pour la section qui
    documente ce manque plutôt que d'inventer un RPC pour l'occasion.
    """

    def __init__(self) -> None:
        address = f"{settings.NOTIFICATION_GRPC_HOST}:{settings.NOTIFICATION_GRPC_PORT}"
        self._channel = canal_authentifie(address, settings.INTERNAL_GRPC_KEY)
        _ensure_proto_in_syspath()

        import notification_service_pb2 as pb
        import notification_service_pb2_grpc as pb_grpc

        self._stub = pb_grpc.NotificationServiceStub(self._channel)
        self._pb = pb

    def list_envois_abonne(self, abonne_id: str) -> list[dict[str, Any]]:
        """Tous les envois WhatsApp ayant ciblé l'abonné.

        Raises:
            grpc.RpcError: si Notification Service est injoignable.
        """
        reponse = self._stub.ListEnvois(self._pb.ListEnvoisRequest(abonne_id=abonne_id))
        return [
            {
                "envoi_id": e.envoi_id,
                "facture_id": e.facture_id,
                "paiement_id": e.paiement_id,
                "type_envoi": e.type_envoi,
                "statut": e.statut,
                "date_envoi": e.date_envoi,
                "erreur": e.erreur,
            }
            for e in reponse.envois
        ]
