import strawberry
from django.conf import settings

from schema.grpc_clients import auth_client


class AuthError(Exception):
    """Erreur d'authentification/autorisation locale (absence de token, rôle insuffisant).

    Pour les erreurs renvoyées par les services gRPC eux-mêmes (token
    invalide/expiré/révoqué, etc.), voir GrpcErrorExtension (extensions.py)
    qui les traduit déjà en GraphQLError lisible — pas besoin de les
    capturer ici.
    """


def extract_token(request) -> str | None:
    header = request.headers.get("Authorization", "")
    if not header.startswith("Bearer "):
        return None
    return header.removeprefix("Bearer ").strip()


def extract_refresh_token(request) -> str | None:
    return request.COOKIES.get(settings.REFRESH_TOKEN_COOKIE_NAME)


def set_refresh_token_cookie(response, refresh_token: str) -> None:
    response.set_cookie(
        settings.REFRESH_TOKEN_COOKIE_NAME,
        refresh_token,
        max_age=settings.REFRESH_TOKEN_COOKIE_MAX_AGE,
        httponly=True,
        secure=settings.REFRESH_TOKEN_COOKIE_SECURE,
        samesite="Strict",
    )


def clear_refresh_token_cookie(response) -> None:
    response.delete_cookie(settings.REFRESH_TOKEN_COOKIE_NAME)


def require_auth(info: strawberry.types.Info):
    """Valide le token de la requête courante auprès de auth-service. Lève AuthError si absent."""
    token = extract_token(info.context["request"])
    if not token:
        raise AuthError("Authentification requise")
    return auth_client.validate_token(token)


def require_role(info: strawberry.types.Info, *roles: str):
    """Valide le token ET vérifie que le rôle de l'utilisateur fait partie de `roles`."""
    user_payload = require_auth(info)
    if user_payload.role not in roles:
        raise AuthError("Accès non autorisé")
    return user_payload
