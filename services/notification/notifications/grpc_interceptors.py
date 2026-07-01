"""Intercepteur gRPC centralisé pour la gestion des erreurs.

Convertit les exceptions Django/métier en codes gRPC appropriés,
sans dupliquer de blocs try/except dans chaque méthode du servicer.
"""

import logging

import grpc
from django.core.exceptions import ObjectDoesNotExist, ValidationError
from django.db import IntegrityError

from notifications.whatsapp_client import WhatsAppDeliveryError

logger = logging.getLogger(__name__)

# Mapping exception → (code gRPC, message override)
# None comme message = utiliser str(exc) directement (message métier déjà sûr)
_STATUS_BY_EXCEPTION = (
    (ObjectDoesNotExist, grpc.StatusCode.NOT_FOUND, None),
    (ValueError, grpc.StatusCode.INVALID_ARGUMENT, None),
    (ValidationError, grpc.StatusCode.INVALID_ARGUMENT, None),
    (IntegrityError, grpc.StatusCode.ALREADY_EXISTS, "Cette ressource existe déjà"),
    (
        WhatsAppDeliveryError,
        grpc.StatusCode.UNAVAILABLE,
        "Échec de l'envoi WhatsApp, réessayez plus tard",
    ),
)


def _abort_for(exc: Exception, context, handler_call_details) -> None:
    """Cherche un mapping pour `exc` et appelle context.abort().

    Si aucun mapping ne correspond, journalise et laisse l'appelant
    relever l'exception d'origine.
    """
    for exc_type, status_code, message in _STATUS_BY_EXCEPTION:
        if isinstance(exc, exc_type):
            if message:
                logger.warning("%s: %s", exc_type.__name__, exc)
            context.abort(status_code, message or str(exc))
            return
    method = getattr(handler_call_details, "method", "?")
    logger.exception("Exception non gérée dans %s", method)


class ErrorHandlingInterceptor(grpc.ServerInterceptor):
    """Convertit les exceptions Django/métier en codes gRPC appropriés."""

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
