import strawberry

from schema.context import (
    AuthError,
    clear_refresh_token_cookie,
    extract_refresh_token,
    extract_token,
    require_role,
    set_refresh_token_cookie,
)
from schema.grpc_clients import auth_client
from schema.types import AuthPayload, Role, User, user_from_grpc


def _auth_payload_from_tokens(token_response) -> AuthPayload:
    user_response = auth_client.get_user(auth_client.validate_token(token_response.access_token).user_id)
    return AuthPayload(
        access_token=token_response.access_token,
        expires_in=token_response.expires_in,
        user=user_from_grpc(user_response),
    )


def _set_password_with_token(token: str, password: str) -> bool:
    # Activation de compte et réinitialisation de mot de passe partagent le
    # même mécanisme côté auth-service (voir SetPasswordWithToken) ; deux
    # noms de mutation distincts ici pour rester clair côté frontend.
    response = auth_client.set_password_with_token(token, password)
    return response.success


@strawberry.type
class Mutation:
    @strawberry.mutation
    def login(self, info: strawberry.types.Info, username: str, password: str) -> AuthPayload:
        # Les erreurs gRPC (identifiants invalides, compte verrouillé, ...)
        # sont traduites en GraphQLError par GrpcErrorExtension.
        token_response = auth_client.login(username, password)
        set_refresh_token_cookie(info.context["response"], token_response.refresh_token)
        return _auth_payload_from_tokens(token_response)

    @strawberry.mutation
    def refresh_token(self, info: strawberry.types.Info) -> AuthPayload:
        refresh_token = extract_refresh_token(info.context["request"])
        if not refresh_token:
            raise AuthError("Refresh token manquant")

        token_response = auth_client.refresh_token(refresh_token)
        set_refresh_token_cookie(info.context["response"], token_response.refresh_token)
        return _auth_payload_from_tokens(token_response)

    @strawberry.mutation
    def logout(self, info: strawberry.types.Info) -> bool:
        token = extract_token(info.context["request"])
        if not token:
            raise AuthError("Authentification requise")
        response = auth_client.logout(token)
        clear_refresh_token_cookie(info.context["response"])
        return response.success

    @strawberry.mutation
    def create_user(self, info: strawberry.types.Info, username: str, email: str, role: Role) -> User:
        # Pas de password : un e-mail d'activation est envoyé à l'utilisateur
        # (voir activateAccount) pour qu'il définisse lui-même son mot de passe.
        require_role(info, "ADMIN")
        user_response = auth_client.create_user(username, email, role.value)
        return user_from_grpc(user_response)

    @strawberry.mutation
    def deactivate_user(self, info: strawberry.types.Info, id: strawberry.ID) -> User:
        require_role(info, "ADMIN")
        user_response = auth_client.deactivate_user(str(id))
        return user_from_grpc(user_response)

    @strawberry.mutation
    def request_password_reset(self, email: str) -> bool:
        # Toujours true, qu'un compte existe ou non pour cet e-mail (ne pas
        # révéler l'existence d'un compte à un appelant non authentifié).
        response = auth_client.request_password_reset(email)
        return response.success

    @strawberry.mutation
    def activate_account(self, token: str, password: str) -> bool:
        return _set_password_with_token(token, password)

    @strawberry.mutation
    def reset_password(self, token: str, password: str) -> bool:
        return _set_password_with_token(token, password)
