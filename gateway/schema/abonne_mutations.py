import strawberry

from schema.abonne_types import (
    Abonne,
    Compteur,
    CreateAbonneInput,
    RemplacerCompteurInput,
    UpdateAbonneInput,
    UpdateCompteurInput,
    abonne_from_grpc,
    compteur_from_grpc,
)
from schema.context import require_role
from schema.grpc_clients import abonne_client


@strawberry.type
class AbonneMutations:
    @strawberry.mutation
    def create_abonne(self, info: strawberry.types.Info, input: CreateAbonneInput) -> Abonne:
        require_role(info, "ADMIN")
        response = abonne_client.create_abonne(
            nom=input.nom,
            prenom=input.prenom,
            telephone_whatsapp=input.telephone_whatsapp,
            adresse=input.adresse or "",
            numero_compteur=input.numero_compteur,
            quartier=input.quartier,
            camp=input.camp,
            index_initial=input.index_initial,
            date_pose=input.date_pose,
        )
        return abonne_from_grpc(response)

    @strawberry.mutation
    def update_abonne(self, info: strawberry.types.Info, id: strawberry.ID, input: UpdateAbonneInput) -> Abonne:
        require_role(info, "ADMIN")
        response = abonne_client.update_abonne(
            str(id),
            nom=input.nom or "",
            prenom=input.prenom or "",
            telephone_whatsapp=input.telephone_whatsapp or "",
            adresse=input.adresse or "",
        )
        return abonne_from_grpc(response)

    @strawberry.mutation
    def suspendre_abonne(self, info: strawberry.types.Info, id: strawberry.ID) -> Abonne:
        require_role(info, "ADMIN")
        return abonne_from_grpc(abonne_client.suspendre_abonne(str(id)))

    @strawberry.mutation
    def reactiver_abonne(self, info: strawberry.types.Info, id: strawberry.ID) -> Abonne:
        require_role(info, "ADMIN")
        return abonne_from_grpc(abonne_client.reactiver_abonne(str(id)))

    @strawberry.mutation
    def resilier_abonne(self, info: strawberry.types.Info, id: strawberry.ID) -> Abonne:
        require_role(info, "ADMIN")
        return abonne_from_grpc(abonne_client.resilier_abonne(str(id)))

    @strawberry.mutation
    def update_compteur(
        self, info: strawberry.types.Info, abonne_id: strawberry.ID, input: UpdateCompteurInput
    ) -> Compteur:
        require_role(info, "ADMIN")
        kwargs = {}
        if input.quartier is not None:
            kwargs["quartier"] = input.quartier
        if input.camp is not None:
            kwargs["camp"] = input.camp
        if input.index_initial is not None:
            kwargs["index_initial"] = input.index_initial
        if input.date_pose is not None:
            kwargs["date_pose"] = input.date_pose
        response = abonne_client.update_compteur(str(abonne_id), **kwargs)
        return compteur_from_grpc(response)

    @strawberry.mutation
    def remplacer_compteur(
        self, info: strawberry.types.Info, abonne_id: strawberry.ID, input: RemplacerCompteurInput
    ) -> Compteur:
        require_role(info, "ADMIN")
        response = abonne_client.remplacer_compteur(
            str(abonne_id),
            index_fermeture=input.index_fermeture,
            nouveau_numero_compteur=input.nouveau_numero_compteur,
            nouveau_quartier=input.nouveau_quartier,
            nouveau_camp=input.nouveau_camp,
            nouvel_index_initial=input.nouvel_index_initial,
            date_remplacement=input.date_remplacement,
            motif=input.motif,
        )
        return compteur_from_grpc(response)
