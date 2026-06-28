import grpc
from django.core.exceptions import ObjectDoesNotExist
from django.db import IntegrityError

from comptes.services import AuthenticationError

# Le 3e élément est le message renvoyé au client : None signifie "utiliser
# str(exc)" (message métier déjà sûr, ex. "Identifiants invalides"). Pour
# IntegrityError, str(exc) est le texte brut du driver SQL (nom de
# contrainte/table) — on le remplace par un message générique pour ne pas
# exposer un détail d'implémentation de la base de données.
_STATUS_BY_EXCEPTION = (
    (AuthenticationError, grpc.StatusCode.UNAUTHENTICATED, None),
    (ObjectDoesNotExist, grpc.StatusCode.NOT_FOUND, None),
    (IntegrityError, grpc.StatusCode.ALREADY_EXISTS, "Cette ressource existe déjà"),
)


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
                for exc_type, status_code, message in _STATUS_BY_EXCEPTION:
                    if isinstance(exc, exc_type):
                        context.abort(status_code, message or str(exc))
                raise

        return grpc.unary_unary_rpc_method_handler(
            wrapped_behavior,
            request_deserializer=handler.request_deserializer,
            response_serializer=handler.response_serializer,
        )
