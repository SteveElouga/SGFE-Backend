"""Mutations GraphQL du Paiement Service."""

import strawberry
import strawberry.types

from .context import require_role
from .grpc_clients import paiement_client
from .paiement_types import Paiement, paiement_from_grpc


@strawberry.type
class PaiementMutations:
    @strawberry.mutation
    def enregistrer_paiement(
        self,
        info: strawberry.types.Info,
        facture_id: str,
        abonne_id: str,
        montant: float,
        date_paiement: str,
        mode_paiement: str,
        reference_transaction: str = "",
    ) -> Paiement:
        """Enregistre un versement sur une facture — ADMIN, COMPTABLE."""
        user = require_role(info, "ADMIN", "COMPTABLE")
        return paiement_from_grpc(
            paiement_client.enregistrer_paiement(
                facture_id=facture_id,
                abonne_id=abonne_id,
                montant=montant,
                date_paiement=date_paiement,
                mode_paiement=mode_paiement,
                reference_transaction=reference_transaction,
                enregistre_par=str(user.user_id),
            ),
            # Le nom d'utilisateur de l'opérateur courant est déjà disponible
            # dans le payload JWT (ValidateToken) — pas besoin d'un aller-retour
            # supplémentaire vers Auth Service pour ce cas précis.
            operateur=user.username,
        )
