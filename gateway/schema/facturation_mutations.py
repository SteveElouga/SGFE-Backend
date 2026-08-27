"""Mutations GraphQL du Facturation Service."""

import grpc
import strawberry
import strawberry.types

from .context import require_auth, require_role
from .facturation_types import Facture, Tarif, facture_from_grpc, tarif_from_grpc
from .grpc_clients import facturation_client, notification_client


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
        envoyer_whatsapp_auto: bool = True,
    ) -> list[Facture]:
        """Génère les factures pour une campagne clôturée — ADMIN, COMPTABLE.

        Utilisé quand generer_factures_auto=false sur la campagne.
        envoyer_whatsapp_auto=true envoie le WhatsApp immédiatement après chaque facture.
        """
        require_auth(info)
        require_role(info, "ADMIN", "COMPTABLE")
        response = facturation_client.generer_factures(
            campagne_id=campagne_id,
            envoyer_whatsapp_auto=envoyer_whatsapp_auto,
        )
        return [facture_from_grpc(f) for f in response.factures]

    @strawberry.mutation
    def envoyer_toutes_factures_whatsapp(
        self,
        info: strawberry.types.Info,
        campagne_id: str,
    ) -> int:
        """Envoie (ou renvoie) le WhatsApp pour toutes les factures d'une campagne — ADMIN, COMPTABLE.

        Parcourt toutes les factures de la campagne et déclenche ReenvoyerFacture
        pour chacune. Retourne le nombre de messages envoyés avec succès.
        Dégradation gracieuse : les échecs individuels n'interrompent pas le lot.
        """
        require_auth(info)
        require_role(info, "ADMIN", "COMPTABLE")
        response = facturation_client.get_factures_par_campagne(campagne_id)
        succes = 0
        for f in response.factures:
            try:
                notification_client.renvoyer_facture(facture_id=f.facture_id)
                succes += 1
            except grpc.RpcError:
                pass
        return succes

    @strawberry.mutation
    def creer_regularisation(
        self,
        info: strawberry.types.Info,
        abonne_id: str,
        montant: float,
        motif: str,
        date_limite_paiement: str | None = None,
    ) -> Facture:
        """Constate à la main une dette antérieure à la mise en service — ADMIN, COMPTABLE.

        Certains abonnés devaient déjà de l'argent avant l'application : ces
        arriérés n'avaient aucun moyen d'entrer dans le système, une facture ne
        naissant que d'un relevé, à la clôture d'une campagne.

        La régularisation est une vraie facture — elle passe donc par tout
        l'aval sans exception : solde, relances, PDF, espace abonné,
        encaissement. Son montant est déclaré et non calculé, d'où le motif
        obligatoire : c'est la seule trace de ce que la dette constate.
        """
        require_auth(info)
        require_role(info, "ADMIN", "COMPTABLE")
        return facture_from_grpc(
            facturation_client.creer_regularisation(
                abonne_id=abonne_id,
                montant=montant,
                motif=motif,
                date_limite_paiement=date_limite_paiement or "",
            )
        )

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
