"""Queries GraphQL des diffusions (Notification Service)."""

import logging

import strawberry
import strawberry.types

from .context import require_role
from .communication_types import Diffusion, diffusion_from_grpc
from .grpc_clients import auth_client, notification_client

logger = logging.getLogger(__name__)


def _resoudre_operateurs(user_ids: set[str]) -> dict[str, str]:
    """Résout un lot d'user_id (created_by) en noms d'utilisateur affichables.

    Même dégradation gracieuse que son équivalent dans `paiement_queries.py` :
    un utilisateur non résolu retombe sur un repli basé sur son identifiant
    plutôt que de faire échouer la liste entière.
    """
    resolus: dict[str, str] = {}
    for user_id in user_ids:
        if not user_id:
            continue
        try:
            resolus[user_id] = auth_client.get_user(user_id).username
        except Exception as exc:
            logger.warning("Impossible de résoudre l'opérateur %s — repli sur l'identifiant", user_id, exc_info=exc)
            resolus[user_id] = f"Utilisateur {user_id[:8]}"
    return resolus


@strawberry.type
class CommunicationQueries:
    @strawberry.field
    def diffusion(self, info: strawberry.types.Info, diffusion_id: str) -> Diffusion:
        """Une diffusion et sa progression courante — ADMIN uniquement."""
        require_role(info, "ADMIN")
        response = notification_client.get_diffusion(diffusion_id)
        operateurs = _resoudre_operateurs({response.created_by})
        return diffusion_from_grpc(response, cree_par=operateurs.get(response.created_by, ""))

    @strawberry.field
    def diffusions(self, info: strawberry.types.Info) -> list[Diffusion]:
        """Historique des diffusions, la plus récente d'abord — ADMIN uniquement."""
        require_role(info, "ADMIN")
        response = notification_client.list_diffusions()
        operateurs = _resoudre_operateurs({d.created_by for d in response.diffusions})
        return [diffusion_from_grpc(d, cree_par=operateurs.get(d.created_by, "")) for d in response.diffusions]
