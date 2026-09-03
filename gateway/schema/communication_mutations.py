"""Mutations GraphQL des diffusions (Notification Service)."""

import strawberry
import strawberry.types

from .context import require_role
from .communication_types import Diffusion, diffusion_from_grpc
from .grpc_clients import notification_client


@strawberry.type
class CommunicationMutations:
    @strawberry.mutation()  # type: ignore[untyped-decorator]  # voir mypy.ini
    def creer_diffusion(self, info: strawberry.types.Info, message: str, abonne_ids: list[str]) -> Diffusion:
        """Lance une diffusion WhatsApp vers les abonnés donnés — ADMIN uniquement.

        Le ciblage (quartier/camp/statut/sélection manuelle) est déjà résolu
        côté frontend en liste concrète d'abonné : cette mutation ne fait que
        créer la diffusion et ses lignes d'envoi. L'envoi lui-même a lieu en
        fond, quelques messages à la fois (`schedulers.diffusion_processor_job`
        côté Notification Service) — jamais dans cette requête, qui rendrait
        sinon le navigateur en attente le temps d'envoyer potentiellement des
        dizaines de messages WhatsApp un par un.
        """
        user = require_role(info, "ADMIN")
        response = notification_client.creer_diffusion(
            message=message, abonne_ids=abonne_ids, created_by=str(user.user_id)
        )
        # Le nom d'utilisateur de l'opérateur courant est déjà dans le payload
        # JWT (ValidateToken) — pas d'aller-retour vers Auth Service ici.
        return diffusion_from_grpc(response, cree_par=user.username)
