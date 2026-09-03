"""Clients gRPC vers les services externes consommés par Reporting Service.

Contrairement à la plupart des clients gRPC du dépôt, ces méthodes ne
dégradent PAS gracieusement (pas de try/except qui avale l'erreur en
retournant une valeur par défaut) : elles ne servent qu'au job de
réconciliation nocturne (stats/schedulers.py), qui doit pouvoir distinguer
« aucune donnée côté service source » de « service source injoignable ». Une
dégradation gracieuse en liste vide écraserait des stats correctes par des
zéros à chaque panne transitoire de Facturation/Paiement — exactement le bug
que la réconciliation est censée corriger, pas reproduire.
"""

import logging
import sys
from pathlib import Path

from django.conf import settings

from stats.dtos import FactureDict, PaiementDict
from stats.grpc_auth import canal_authentifie

logger = logging.getLogger(__name__)


class FacturationServiceClient:
    """Client gRPC vers Facturation Service (port 50054) — source de vérité des factures."""

    def __init__(self) -> None:
        address = f"{settings.FACTURATION_GRPC_HOST}:{settings.FACTURATION_GRPC_PORT}"
        self._channel = canal_authentifie(address, settings.INTERNAL_GRPC_KEY)

        proto_path = str(Path(settings.BASE_DIR) / "proto")
        if proto_path not in sys.path:
            sys.path.insert(0, proto_path)

        import facturation_service_pb2 as pb
        import facturation_service_pb2_grpc as pb_grpc

        self._stub = pb_grpc.FacturationServiceStub(self._channel)
        self._pb = pb

    def list_factures_par_campagne(self, campagne_id: str) -> list[FactureDict]:
        """Retourne toutes les factures (tous statuts, y compris ANNULEE) d'une campagne.

        Lève l'exception gRPC si Facturation Service est inaccessible — voir la
        docstring du module : c'est volontaire, l'appelant doit le savoir.
        """
        response = self._stub.GetFacturesParCampagne(self._pb.CampagneIdRequest(campagne_id=campagne_id))
        return [
            {
                "facture_id": f.facture_id,
                "statut": f.statut,
                "montant": f.montant,
            }
            for f in response.factures
        ]


class PaiementServiceClient:
    """Client gRPC vers Paiement Service (port 50055) — source de vérité des versements."""

    def __init__(self) -> None:
        address = f"{settings.PAIEMENT_GRPC_HOST}:{settings.PAIEMENT_GRPC_PORT}"
        self._channel = canal_authentifie(address, settings.INTERNAL_GRPC_KEY)

        proto_path = str(Path(settings.BASE_DIR) / "proto")
        if proto_path not in sys.path:
            sys.path.insert(0, proto_path)

        import paiement_service_pb2 as pb
        import paiement_service_pb2_grpc as pb_grpc

        self._stub = pb_grpc.PaiementServiceStub(self._channel)
        self._pb = pb

    def list_paiements_par_campagne(self, campagne_id: str) -> list[PaiementDict]:
        """Retourne tous les paiements (y compris annulés) des factures d'une campagne.

        Lève l'exception gRPC si Paiement Service est inaccessible — voir la
        docstring du module.
        """
        response = self._stub.ListPaiementsParCampagne(self._pb.CampagneIdRequest(campagne_id=campagne_id))
        return [
            {
                "montant": p.montant,
                "annule": p.annule,
            }
            for p in response.paiements
        ]
