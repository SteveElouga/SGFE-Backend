import logging

import grpc
from django.core.exceptions import ObjectDoesNotExist, ValidationError

logger = logging.getLogger(__name__)

# Mapping exception -> (code gRPC, message). message=None => on renvoie str(exc).
# Centralise ce mapping une seule fois plutôt que de le répéter dans un
# try/except à chaque méthode du servicer.
_STATUS_BY_EXCEPTION = (
    (ObjectDoesNotExist, grpc.StatusCode.NOT_FOUND, None),
    (ValidationError, grpc.StatusCode.INVALID_ARGUMENT, None),
)


def _abort_for(exc: Exception, context, handler_call_details) -> None:
    """Cherche un mapping pour `exc` et appelle context.abort() (qui lève).
    Sans mapping : journalise et laisse l'appelant relever l'exception d'origine
    (le framework gRPC renverra alors UNKNOWN, comme pour les autres services)."""
    for exc_type, status_code, message in _STATUS_BY_EXCEPTION:
        if isinstance(exc, exc_type):
            context.abort(status_code, message or str(exc))
            return
    method = getattr(handler_call_details, "method", "?")
    logger.exception("Exception non gérée dans %s", method)


class ErrorHandlingInterceptor(grpc.ServerInterceptor):
    """Convertit les exceptions Django/métier en codes gRPC appropriés.

    Centralise le mapping (voir `_STATUS_BY_EXCEPTION`) une seule fois plutôt
    que de répéter un try/except dans chaque méthode du servicer.
    """

    def intercept_service(self, continuation, handler_call_details):
        handler = continuation(handler_call_details)
        if handler is None or not handler.unary_unary:
            return handler

        original_behavior = handler.unary_unary

        def wrapped_behavior(request, context):
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
