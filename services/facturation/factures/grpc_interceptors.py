from __future__ import annotations

import logging
from typing import Any, Callable

import grpc
from django.core.exceptions import ObjectDoesNotExist, ValidationError

from .exceptions import PreconditionError

logger = logging.getLogger(__name__)

# Mapping exception -> (code gRPC, message). message=None => on renvoie str(exc).
# L'ordre compte : PreconditionError (sous-classe de ValidationError) doit être
# testée AVANT ValidationError pour être mappée en FAILED_PRECONDITION.
_STATUS_BY_EXCEPTION = (
    (PreconditionError, grpc.StatusCode.FAILED_PRECONDITION, None),
    (ObjectDoesNotExist, grpc.StatusCode.NOT_FOUND, None),
    (ValidationError, grpc.StatusCode.INVALID_ARGUMENT, None),
    (grpc.RpcError, grpc.StatusCode.UNAVAILABLE, "Service en amont indisponible, réessayez plus tard"),
    (FileNotFoundError, grpc.StatusCode.INTERNAL, None),
)


def _abort_for(exc: Exception, context: grpc.ServicerContext, handler_call_details: grpc.HandlerCallDetails) -> None:
    """Cherche un mapping pour `exc` et appelle context.abort() (qui lève).
    Sans mapping : journalise et laisse l'appelant relever l'exception d'origine
    (le framework gRPC renverra alors UNKNOWN, comme pour les autres services)."""
    for exc_type, status_code, message in _STATUS_BY_EXCEPTION:
        if isinstance(exc, exc_type):
            if message:
                logger.warning("%s: %s", exc_type.__name__, exc)
            context.abort(status_code, message or str(exc))
            return
    method = getattr(handler_call_details, "method", "?")
    logger.exception("Exception non gérée dans %s", method)


class ErrorHandlingInterceptor(grpc.ServerInterceptor):
    """Convertit les exceptions Django/métier en codes gRPC appropriés.

    Centralise le mapping (voir `_STATUS_BY_EXCEPTION`) une seule fois plutôt
    que de répéter un try/except dans chaque méthode du servicer.
    """

    def intercept_service(
        self,
        continuation: Callable[[grpc.HandlerCallDetails], grpc.RpcMethodHandler[Any, Any] | None],
        handler_call_details: grpc.HandlerCallDetails,
    ) -> grpc.RpcMethodHandler[Any, Any] | None:
        handler = continuation(handler_call_details)
        if handler is None or not handler.unary_unary:
            return handler

        original_behavior = handler.unary_unary

        def wrapped_behavior(request: Any, context: grpc.ServicerContext) -> Any:
            try:
                return original_behavior(request, context)
            except Exception as exc:
                _abort_for(exc, context, handler_call_details)
                raise

        return grpc.unary_unary_rpc_method_handler(
            wrapped_behavior,
            request_deserializer=handler.request_deserializer,
            response_serializer=handler.response_serializer,
        )
