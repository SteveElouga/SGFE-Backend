import strawberry

from schema.config_types import ConfigParam, InfosSociete, config_from_grpc, infos_from_grpc
from schema.context import require_role
from schema.grpc_clients import config_client


@strawberry.type
class ConfigQueries:
    @strawberry.field
    def infos_societe(self, info: strawberry.types.Info) -> InfosSociete:
        """Informations de la société (apparaissent sur les factures PDF)."""
        response = config_client.get_infos_societe()
        return infos_from_grpc(response)

    @strawberry.field
    def config(self, info: strawberry.types.Info, cle: str) -> ConfigParam:
        """Paramètre de configuration par clé — ADMIN uniquement."""
        require_role(info, "ADMIN")
        response = config_client.get_config(cle)
        return config_from_grpc(response)

    @strawberry.field
    def configs(self, info: strawberry.types.Info) -> list[ConfigParam]:
        """Liste tous les paramètres de configuration — ADMIN uniquement."""
        require_role(info, "ADMIN")
        response = config_client.list_configs()
        return [config_from_grpc(c) for c in response.configs]
