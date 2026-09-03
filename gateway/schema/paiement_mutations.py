"""Mutations GraphQL du Paiement Service."""

import strawberry
import strawberry.types

from .context import require_auth, require_role
from .grpc_clients import paiement_client
from .paiement_types import (
    Avoir,
    Paiement,
    PaiementAbonne,
    avoir_from_grpc,
    paiement_from_grpc,
)


@strawberry.type
class PaiementMutations:
    @strawberry.mutation()  # type: ignore[untyped-decorator]  # voir mypy.ini
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

    @strawberry.mutation()  # type: ignore[untyped-decorator]  # voir mypy.ini
    def annuler_paiement(
        self,
        info: strawberry.types.Info,
        paiement_id: str,
        motif: str,
    ) -> Paiement:
        """Annule un paiement enregistré par erreur (rétablit le solde) — ADMIN, COMPTABLE."""
        user = require_role(info, "ADMIN", "COMPTABLE")
        return paiement_from_grpc(
            paiement_client.annuler_paiement(
                paiement_id=paiement_id,
                motif=motif,
                annule_par=str(user.user_id),
            ),
            operateur=user.username,
        )

    @strawberry.mutation()  # type: ignore[untyped-decorator]  # voir mypy.ini
    def crediter_avoir(
        self,
        info: strawberry.types.Info,
        abonne_id: str,
        montant: float,
        motif: str,
    ) -> Avoir:
        """Émet un avoir manuel (note de rectification) sur le compte d'un abonné —
        reporté automatiquement sur ses prochaines factures — ADMIN, COMPTABLE."""
        user = require_role(info, "ADMIN", "COMPTABLE")
        return avoir_from_grpc(
            paiement_client.crediter_avoir(
                abonne_id=abonne_id,
                montant=montant,
                motif=motif,
                cree_par=str(user.user_id),
            )
        )

    @strawberry.mutation()  # type: ignore[untyped-decorator]  # voir mypy.ini
    def enregistrer_paiement_abonne(
        self,
        info: strawberry.types.Info,
        abonne_id: str,
        montant: float,
        date_paiement: str,
        mode_paiement: str,
        reference_transaction: str = "",
    ) -> PaiementAbonne:
        """Encaisse un versement au nom d'un abonné — ADMIN, COMPTABLE.

        L'imputation va du plus ancien au plus récent : le caissier saisit un
        montant, le système répartit. C'est le geste courant — un abonné qui
        tend de l'argent paie sa dette, pas une facture qu'il aurait choisie.

        `enregistrerPaiement` (une facture nommée) reste disponible pour les cas
        où l'imputation doit être forcée : contestation d'une facture précise,
        régularisation d'écriture.
        """
        # `require_auth` renvoie l'utilisateur validé — le contexte, lui,
        # n'expose pas `.user`. C'est ainsi que procède `enregistrerPaiement`.
        user = require_auth(info)
        require_role(info, "ADMIN", "COMPTABLE")
        r = paiement_client.enregistrer_paiement_abonne(
            abonne_id=abonne_id,
            montant=montant,
            date_paiement=date_paiement,
            mode_paiement=mode_paiement,
            reference_transaction=reference_transaction,
            enregistre_par=str(user.user_id),
        )
        return PaiementAbonne(
            paiements=[paiement_from_grpc(p) for p in r.paiements],
            excedent_en_avoir=r.excedent_en_avoir,
        )
