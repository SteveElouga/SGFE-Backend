import strawberry

from schema.context import AuthError, extract_token, require_role
from schema.grpc_clients import auth_client
from schema.types import AuthPayload, Role, User, user_from_grpc


def _auth_payload_from_tokens(token_response) -> AuthPayload:
    user_response = auth_client.get_user(auth_client.validate_token(token_response.access_token).user_id)
    return AuthPayload(
        access_token=token_response.access_token,
        refresh_token=token_response.refresh_token,
        expires_in=token_response.expires_in,
        user=user_from_grpc(user_response),
    )


@strawberry.type
class Mutation:
    @strawberry.mutation
    def login(self, username: str, password: str) -> AuthPayload:
        # Les erreurs gRPC (identifiants invalides, compte verrouillé, ...)
        # sont traduites en GraphQLError par GrpcErrorExtension.
        token_response = auth_client.login(username, password)
        return _auth_payload_from_tokens(token_response)

    @strawberry.mutation
    def refresh_token(self, token: str) -> AuthPayload:
        token_response = auth_client.refresh_token(token)
        return _auth_payload_from_tokens(token_response)

    @strawberry.mutation
    def logout(self, info: strawberry.types.Info) -> bool:
        token = extract_token(info.context["request"])
        if not token:
            raise AuthError("Authentification requise")
        response = auth_client.logout(token)
        return response.success

    @strawberry.mutation
    def create_user(self, info: strawberry.types.Info, username: str, email: str, password: str, role: Role) -> User:
        require_role(info, "ADMIN")
        user_response = auth_client.create_user(username, email, password, role.value)
        return user_from_grpc(user_response)

    @strawberry.mutation
    def deactivate_user(self, info: strawberry.types.Info, id: strawberry.ID) -> User:
        require_role(info, "ADMIN")
        user_response = auth_client.deactivate_user(str(id))
        return user_from_grpc(user_response)
