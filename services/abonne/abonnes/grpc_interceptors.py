import logging

import grpc
from django.core.exceptions import ObjectDoesNotExist
from django.db import IntegrityError

from abonnes.services import ValidationError

logger = logging.getLogger(__name__)

# Le 3e élément est le message renvoyé au client : None signifie "utiliser
# str(exc)" (message métier déjà sûr). Pour IntegrityError, str(exc) est le
# texte brut du driver SQL — remplacé par un message générique.
_STATUS_BY_EXCEPTION = (
    (ValidationError, grpc.StatusCode.INVALID_ARGUMENT, None),
    (ObjectDoesNotExist, grpc.StatusCode.NOT_FOUND, None),
    (IntegrityError, grpc.StatusCode.ALREADY_EXISTS, "Cette ressource existe déjà"),
)


def _abort_for(exc: Exception, context, handler_call_details) -> None:
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

    Centralise ce mapping une seule fois plutôt que de répéter un
    try/except dans chaque méthode de chaque servicer.
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
