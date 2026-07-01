"""Mutations GraphQL du Facturation Service."""

import strawberry
import strawberry.types

from .context import require_auth, require_role
from .facturation_types import Facture, Tarif, facture_from_grpc, tarif_from_grpc
from .grpc_clients import facturation_client


@strawberry.type
class FacturationMutations:
    @strawberry.mutation
    def update_tarif(
        self,
        info: strawberry.types.Info,
        prix_m3: float,
        date_effet: str,
    ) -> Tarif:
        """Modifie le prix du m³ (désactive l'ancien, crée le nouveau) — ADMIN uniquement."""
        require_auth(info)
        require_role(info, "ADMIN")
        return tarif_from_grpc(facturation_client.update_tarif(prix_m3=prix_m3, date_effet=date_effet))

    @strawberry.mutation
    def generer_factures(
        self,
        info: strawberry.types.Info,
        campagne_id: str,
    ) -> list[Facture]:
        """Génère les factures pour une campagne clôturée — ADMIN uniquement.

        Normalement déclenché automatiquement par CloturerCampagne.
        Cette mutation permet un déclenchement manuel si nécessaire.
        """
        require_auth(info)
        require_role(info, "ADMIN")
        response = facturation_client.generer_factures(campagne_id)
        return [facture_from_grpc(f) for f in response.factures]

    @strawberry.mutation
    def update_statut_facture(
        self,
        info: strawberry.types.Info,
        facture_id: str,
        statut: str,
    ) -> Facture:
        """Met à jour le statut d'une facture — ADMIN, COMPTABLE.

        Normalement appelé par Paiement Service.
        Cette mutation permet une correction manuelle si nécessaire.
        """
        require_auth(info)
        require_role(info, "ADMIN", "COMPTABLE")
        return facture_from_grpc(facturation_client.update_statut_facture(facture_id=facture_id, statut=statut))
