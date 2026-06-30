import strawberry
import strawberry.types

from schema.abonne_types import Abonne, HistoriqueCompteur, StatutAbonne, abonne_from_grpc, historique_from_grpc
from schema.context import require_role
from schema.grpc_clients import abonne_client


@strawberry.type
class AbonneQueries:
    @strawberry.field
    def abonne(self, info: strawberry.types.Info, id: strawberry.ID) -> Abonne | None:
        """Détails d'un abonné — ADMIN uniquement."""
        require_role(info, "ADMIN")
        return abonne_from_grpc(abonne_client.get_abonne(str(id)))

    @strawberry.field
    def abonnes(self, info: strawberry.types.Info, statut: StatutAbonne | None = None) -> list[Abonne]:
        """Liste des abonnés — ADMIN uniquement."""
        require_role(info, "ADMIN")
        response = abonne_client.list_abonnes(statut.value if statut else "")
        return [abonne_from_grpc(a) for a in response.abonnes]

    @strawberry.field
    def abonnes_actifs(self, info: strawberry.types.Info) -> list[Abonne]:
        """Liste des abonnés ACTIF — ADMIN et SUPERVISEUR (sélecteur pour création de campagne)."""
        require_role(info, "ADMIN", "SUPERVISEUR")
        response = abonne_client.list_abonnes_actifs()
        return [abonne_from_grpc(a) for a in response.abonnes]

    @strawberry.field
    def historique_compteur(self, info: strawberry.types.Info, id: strawberry.ID) -> list[HistoriqueCompteur]:
        """Historique des remplacements de compteur pour un abonné — ADMIN uniquement."""
        require_role(info, "ADMIN")
        response = abonne_client.get_historique_compteur(str(id))
        return [historique_from_grpc(h) for h in response.historique]
