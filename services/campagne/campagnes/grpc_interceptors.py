"""Intercepteur gRPC pour l'authentification JWT du Campagne Service."""

import logging
from typing import Any, Callable

import grpc
import jwt
from django.conf import settings

logger = logging.getLogger(__name__)

# Méthodes publiques (pas de JWT requis)
PUBLIC_METHODS: set[str] = set()


class JWTAuthInterceptor(grpc.ServerInterceptor):
    """Valide le JWT Bearer dans les métadonnées gRPC."""

    def intercept_service(
        self,
        continuation: Callable,
        handler_call_details: grpc.HandlerCallDetails,
    ) -> Any:
        method = handler_call_details.method.split("/")[-1]
        if method in PUBLIC_METHODS:
            return continuation(handler_call_details)

        metadata = dict(handler_call_details.invocation_metadata or [])
        token = metadata.get("authorization", "").removeprefix("Bearer ").strip()

        if not token:
            return self._abort(grpc.StatusCode.UNAUTHENTICATED, "Token JWT manquant.")

        try:
            jwt.decode(
                token,
                settings.JWT_SECRET_KEY,
                algorithms=[settings.JWT_ALGORITHM],
            )
        except jwt.ExpiredSignatureError:
            return self._abort(grpc.StatusCode.UNAUTHENTICATED, "Token JWT expiré.")
        except jwt.InvalidTokenError as exc:
            return self._abort(
                grpc.StatusCode.UNAUTHENTICATED, f"Token JWT invalide : {exc}"
            )

        return continuation(handler_call_details)

    @staticmethod
    def _abort(code: grpc.StatusCode, details: str) -> grpc.RpcMethodHandler:
        def handler(request: Any, context: grpc.ServicerContext) -> None:
            context.abort(code, details)

        return grpc.unary_unary_rpc_method_handler(handler)
