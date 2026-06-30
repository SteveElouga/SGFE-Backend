import inspect

import grpc
from graphql import GraphQLError
from strawberry.extensions import SchemaExtension

# Code GraphQL (extensions.code) par code gRPC — utilisé par le frontend
# pour distinguer les types d'erreur sans parser le message texte.
_CODE_BY_STATUS = {
    grpc.StatusCode.UNAUTHENTICATED: "UNAUTHENTICATED",
    grpc.StatusCode.PERMISSION_DENIED: "PERMISSION_DENIED",
    grpc.StatusCode.NOT_FOUND: "NOT_FOUND",
    grpc.StatusCode.ALREADY_EXISTS: "ALREADY_EXISTS",
    grpc.StatusCode.INVALID_ARGUMENT: "INVALID_ARGUMENT",
    grpc.StatusCode.UNAVAILABLE: "SERVICE_UNAVAILABLE",
}

_MESSAGE_BY_STATUS = {
    grpc.StatusCode.UNAUTHENTICATED: "Authentification requise ou invalide",
    grpc.StatusCode.PERMISSION_DENIED: "Accès non autorisé",
    grpc.StatusCode.NOT_FOUND: "Ressource introuvable",
    grpc.StatusCode.ALREADY_EXISTS: "Cette ressource existe déjà",
    grpc.StatusCode.INVALID_ARGUMENT: "Paramètre invalide",
    grpc.StatusCode.UNAVAILABLE: "Service temporairement indisponible",
}


def _build_graphql_error(exc: grpc.RpcError) -> GraphQLError:
    """Construit un GraphQLError structuré depuis une exception gRPC.

    - `message` : détail métier renvoyé par le service (ex. "Code OTP invalide ou expiré").
    - `extensions.code` : code machine stable pour le frontend (ex. "UNAUTHENTICATED").
    """
    code = exc.code() if hasattr(exc, "code") else None
    details = exc.details() if hasattr(exc, "details") else None
    message = details or _MESSAGE_BY_STATUS.get(code, "Erreur du service distant")
    error_code = _CODE_BY_STATUS.get(code, "INTERNAL_ERROR")
    return GraphQLError(message, extensions={"code": error_code})


class GrpcErrorExtension(SchemaExtension):
    """Traduit toute `grpc.RpcError` levée par un resolver en `GraphQLError`
    lisible, avec un `extensions.code` stable pour le frontend.

    Centralisé ici une seule fois pour tous les resolvers/mutations, présents
    et futurs, au lieu d'un try/except répété dans chacun.
    """

    def resolve(self, _next, root, info, *args, **kwargs):
        try:
            result = _next(root, info, *args, **kwargs)
        except grpc.RpcError as exc:
            raise _build_graphql_error(exc) from exc

        if inspect.isawaitable(result):
            return self._await_and_translate(result)
        return result

    @staticmethod
    async def _await_and_translate(awaitable):
        try:
            return await awaitable
        except grpc.RpcError as exc:
            raise _build_graphql_error(exc) from exc
