import strawberry

from schema.config_types import ConfigParam, InfosSociete, config_from_grpc, infos_from_grpc
from schema.context import require_role
from schema.grpc_clients import config_client


@strawberry.input
class UpdateInfosSocieteInput:
    nom: str = ""
    adresse: str = ""
    telephone: str = ""
    logo_path: str = ""


@strawberry.type
class ConfigMutations:
    @strawberry.mutation
    def update_infos_societe(self, info: strawberry.types.Info, input: UpdateInfosSocieteInput) -> InfosSociete:
        """Met à jour les informations de la société — ADMIN uniquement."""
        require_role(info, "ADMIN")
        response = config_client.update_infos_societe(
            nom=input.nom,
            adresse=input.adresse,
            telephone=input.telephone,
            logo_path=input.logo_path,
        )
        return infos_from_grpc(response)

    @strawberry.mutation
    def update_config(self, info: strawberry.types.Info, cle: str, valeur: str) -> ConfigParam:
        """Met à jour un paramètre de configuration — ADMIN uniquement."""
        require_role(info, "ADMIN")
        response = config_client.update_config(cle=cle, valeur=valeur)
        return config_from_grpc(response)
