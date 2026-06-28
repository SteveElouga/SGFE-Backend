import strawberry

from schema.context import require_auth
from schema.grpc_clients import auth_client
from schema.types import User, user_from_grpc


@strawberry.type
class Query:
    @strawberry.field
    def me(self, info: strawberry.types.Info) -> User | None:
        user_payload = require_auth(info)
        user_response = auth_client.get_user(user_payload.user_id)
        return user_from_grpc(user_response)
