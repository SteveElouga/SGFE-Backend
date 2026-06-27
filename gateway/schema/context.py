import grpc
import strawberry

from schema.grpc_clients import auth_client


class AuthError(Exception):
    """Erreur d'authentification/autorisation GraphQL (401/403)."""


def extract_token(request) -> str | None:
    header = request.headers.get("Authorization", "")
    if not header.startswith("Bearer "):
        return None
    return header.removeprefix("Bearer ").strip()


def require_auth(info: strawberry.types.Info):
    """Valide le token de la requête courante auprès de auth-service. Lève AuthError sinon."""
    token = extract_token(info.context["request"])
    if not token:
        raise AuthError("Authentification requise")

    try:
        return auth_client.validate_token(token)
    except grpc.RpcError as exc:
        raise AuthError("Token invalide ou expiré") from exc


def require_role(info: strawberry.types.Info, *roles: str):
    """Valide le token ET vérifie que le rôle de l'utilisateur fait partie de `roles`."""
    user_payload = require_auth(info)
    if user_payload.role not in roles:
        raise AuthError("Accès non autorisé")
    return user_payload
