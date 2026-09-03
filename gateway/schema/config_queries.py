import strawberry

from schema.config_types import ConfigParam, InfosSociete, config_from_grpc, infos_from_grpc
from schema.context import require_auth, require_role
from schema.grpc_clients import config_client


@strawberry.type
class ConfigQueries:
    @strawberry.field
    def infos_societe(self, info: strawberry.types.Info) -> InfosSociete:
        """Informations de la société.

        Authentification requise. `docs/ARCHITECTURE.md` et
        `docs/ETAT_DU_SYSTEME.md` documentaient cette query comme
        volontairement publique au motif qu'elle « alimente les PDF de
        facture » — mais ce PDF est rendu côté `facturation-service`, qui
        appelle `config-service` directement en gRPC (authentifié par
        INTERNAL_GRPC_KEY), jamais via cette query GraphQL. Vérifié
        empiriquement côté frontend (grep sur `infosSociete` dans
        SGFE-frontend/src) : le seul consommateur est l'écran
        `features/configuration/`, derrière `roleGuard(['ADMIN'])`. Aucun
        écran non authentifié (login, espace-abonné public) ne l'utilise.
        L'exception documentée ne correspondait donc à aucun besoin réel —
        fermée ici (voir AUDIT_SGFE.md §Sécurité).
        """
        require_auth(info)
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
