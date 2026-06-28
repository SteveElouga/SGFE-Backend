import inspect

import grpc
from graphql import GraphQLError
from strawberry.extensions import SchemaExtension

_MESSAGE_BY_STATUS = {
    grpc.StatusCode.UNAUTHENTICATED: "Authentification requise ou invalide",
    grpc.StatusCode.PERMISSION_DENIED: "Accès non autorisé",
    grpc.StatusCode.NOT_FOUND: "Ressource introuvable",
    grpc.StatusCode.ALREADY_EXISTS: "Cette ressource existe déjà",
}


def _clean_message(exc: grpc.RpcError) -> str:
    """Préfère le message métier renvoyé par le service (`details()`), sinon
    retombe sur un message générique basé sur le code de statut gRPC."""
    details = exc.details() if hasattr(exc, "details") else None
    code = exc.code() if hasattr(exc, "code") else None
    return details or _MESSAGE_BY_STATUS.get(code, "Erreur du service distant")


class GrpcErrorExtension(SchemaExtension):
    """Traduit toute `grpc.RpcError` levée par un resolver en `GraphQLError`
    lisible, plutôt que de laisser fuiter la représentation brute de
    l'exception gRPC (stack trace, codes internes) jusqu'au client.

    Centralisé ici une seule fois pour tous les resolvers/mutations, présents
    et futurs, au lieu d'un try/except répété dans chacun.
    """

    def resolve(self, _next, root, info, *args, **kwargs):
        try:
            result = _next(root, info, *args, **kwargs)
        except grpc.RpcError as exc:
            raise GraphQLError(_clean_message(exc)) from exc

        if inspect.isawaitable(result):
            return self._await_and_translate(result)
        return result

    @staticmethod
    async def _await_and_translate(awaitable):
        try:
            return await awaitable
        except grpc.RpcError as exc:
            raise GraphQLError(_clean_message(exc)) from exc
