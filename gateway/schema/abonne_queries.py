import strawberry

from schema.abonne_types import Abonne, HistoriqueCompteur, StatutAbonne, abonne_from_grpc, historique_from_grpc
from schema.grpc_clients import abonne_client


@strawberry.type
class AbonneQueries:
    @strawberry.field
    def abonne(self, id: strawberry.ID) -> Abonne | None:
        return abonne_from_grpc(abonne_client.get_abonne(str(id)))

    @strawberry.field
    def abonnes(self, statut: StatutAbonne | None = None) -> list[Abonne]:
        response = abonne_client.list_abonnes(statut.value if statut else "")
        return [abonne_from_grpc(a) for a in response.abonnes]

    @strawberry.field
    def abonnes_actifs(self) -> list[Abonne]:
        """Liste des abonnés ACTIF uniquement — utilisé par campagne-service et les sélecteurs frontend."""
        response = abonne_client.list_abonnes_actifs()
        return [abonne_from_grpc(a) for a in response.abonnes]

    @strawberry.field
    def historique_compteur(self, id: strawberry.ID) -> list[HistoriqueCompteur]:
        """Historique des remplacements de compteur pour un abonné (EF-ABO-006)."""
        response = abonne_client.get_historique_compteur(str(id))
        return [historique_from_grpc(h) for h in response.historique]
