import strawberry

from schema.abonne_types import Abonne, StatutAbonne, abonne_from_grpc
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
