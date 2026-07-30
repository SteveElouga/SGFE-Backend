"""Query GraphQL `statsParMois` — agrégat mensuel réel.

Le Reporting Service ne stocke que des totaux par campagne, sans dimension
temporelle (et son pipeline d'événements n'est pas branché) : impossible d'en
tirer un « encaissé par mois ». On calcule donc l'agrégat ici, par fan-out
cross-service (même patron que les autres résolveurs de la gateway), à partir
des dates faisant autorité : `Paiement.date_paiement` et `Facture.date_generation`.

Portée : ADMIN/COMPTABLE voient toutes les campagnes ; un SUPERVISEUR ne voit
que les siennes (`Campagne.created_by == user.user_id`), filtrées au resolver.
"""

import strawberry
import strawberry.types

from schema.context import require_role
from schema.grpc_clients import campagne_client, facturation_client, paiement_client
from schema.reporting_types import StatMois, build_stats_par_mois


@strawberry.type
class StatsQueries:
    @strawberry.field
    def stats_par_mois(self, info: strawberry.types.Info, nb_mois: int = 12) -> list[StatMois]:
        """Agrégat mensuel réel sur les `nbMois` derniers mois glissants, trié du
        plus récent ([0] = mois courant) au plus ancien (zéros compris). ADMIN et
        COMPTABLE : global ; SUPERVISEUR : uniquement ses campagnes."""
        user = require_role(info, "ADMIN", "COMPTABLE", "SUPERVISEUR")
        nb = max(1, min(nb_mois, 120))  # borne défensive contre un nbMois absurde

        # SUPERVISEUR : restreint aux campagnes qu'il a créées (comme les autres
        # queries campagne). ADMIN/COMPTABLE : created_by vide = toutes.
        created_by = user.user_id if user.role == "SUPERVISEUR" else ""
        campagnes = campagne_client.list_campagnes(created_by=created_by).campagnes

        # Fan-out : on collecte factures + paiements de chaque campagne du périmètre,
        # puis on agrège par mois en mémoire. Coût = N campagnes × 2 appels gRPC ;
        # acceptable à cette échelle, à revisiter (table mensuelle côté Reporting)
        # si le nombre de campagnes explose.
        factures: list = []
        paiements: list = []
        for c in campagnes:
            factures.extend(facturation_client.get_factures_par_campagne(c.campagne_id).factures)
            paiements.extend(paiement_client.list_paiements_par_campagne(c.campagne_id).paiements)

        return build_stats_par_mois(factures, paiements, nb)
