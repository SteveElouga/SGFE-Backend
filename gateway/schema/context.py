import logging

import strawberry
from django.conf import settings

from schema.grpc_clients import auth_client

logger = logging.getLogger(__name__)


class AuthError(Exception):
    """Erreur d'authentification/autorisation locale (absence de token, rôle insuffisant).

    `code` est le code machine stable renvoyé au frontend via extensions.code :
    - "UNAUTHENTICATED" : token absent ou invalide
    - "PERMISSION_DENIED" : rôle insuffisant

    Pour les erreurs renvoyées par les services gRPC eux-mêmes, voir
    GrpcErrorExtension (extensions.py) qui les traduit déjà.
    """

    def __init__(self, message: str, code: str = "UNAUTHENTICATED") -> None:
        super().__init__(message)
        self.code = code


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


def _token_from_connection_params(info: strawberry.types.Info) -> str | None:
    """Récupère le JWT depuis les `connectionParams` d'une subscription WebSocket.

    Sur une connexion WebSocket (graphql-ws / graphql-transport-ws), le token ne
    peut pas transiter par un header HTTP `Authorization` : le client
    (Apollo/graphql-ws) l'envoie dans le payload `connection_init`, exposé par
    Strawberry sous `info.context["connection_params"]`. Tolérant aux conventions
    courantes : `Authorization`/`authorization` (avec ou sans préfixe `Bearer`),
    ou un token brut sous `token`/`authToken`.
    """
    context = info.context
    params = (
        context.get("connection_params") if isinstance(context, dict) else getattr(context, "connection_params", None)
    )
    if not params:
        return None

    raw = (
        params.get("Authorization")
        or params.get("authorization")
        or params.get("token")
        or params.get("authToken")
        or ""
    ).strip()
    if raw.lower().startswith("bearer "):
        raw = raw[len("bearer ") :].strip()
    if not raw:
        # Aide au diagnostic si le frontend envoie le token sous une autre clé
        # (on ne journalise que les clés, jamais les valeurs).
        logger.warning(
            "Subscription WS : aucun token reconnu dans connectionParams (clés : %s)",
            list(params.keys()),
        )
    return raw or None


def require_auth(info: strawberry.types.Info):
    """Valide le token de la requête courante auprès de auth-service. Lève AuthError si absent.

    Le token provient du header HTTP `Authorization` (queries/mutations) ou, pour
    les subscriptions WebSocket, des `connectionParams` (voir
    `_token_from_connection_params`).
    """
    token = extract_token(info.context["request"]) or _token_from_connection_params(info)
    if not token:
        raise AuthError("Authentification requise", code="UNAUTHENTICATED")
    return auth_client.validate_token(token)


def require_role(info: strawberry.types.Info, *roles: str):
    """Valide le token ET vérifie que le rôle de l'utilisateur fait partie de `roles`."""
    user_payload = require_auth(info)
    if user_payload.role not in roles:
        raise AuthError("Accès non autorisé", code="PERMISSION_DENIED")
    return user_payload
