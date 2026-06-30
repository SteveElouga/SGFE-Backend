import strawberry

from schema.auth_types import User, user_from_grpc
from schema.context import require_auth, require_role
from schema.grpc_clients import auth_client


@strawberry.type
class AuthQueries:
    @strawberry.field
    def me(self, info: strawberry.types.Info) -> User | None:
        user_payload = require_auth(info)
        user_response = auth_client.get_user(user_payload.user_id)
        return user_from_grpc(user_response)

    @strawberry.field
    def users(self, info: strawberry.types.Info) -> list[User]:
        """Liste tous les utilisateurs — ADMIN uniquement."""
        require_role(info, "ADMIN")
        response = auth_client.list_users()
        return [user_from_grpc(u) for u in response.users]
