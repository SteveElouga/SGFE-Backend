"""Tests de l'ErrorHandlingInterceptor du Auth Service (mapping exception -> code gRPC)."""

from typing import Any
from unittest.mock import MagicMock

import grpc
from django.core.exceptions import ObjectDoesNotExist
from django.db import IntegrityError
from django.test import SimpleTestCase

from comptes.email_client import EmailDeliveryError
from comptes.grpc_interceptors import ErrorHandlingInterceptor
from comptes.services import AuthenticationError
from comptes.throttle import ThrottleError
from comptes.whatsapp_client import WhatsAppDeliveryError


def _wrapped_behavior(exc: Exception) -> Any:
    """Construit un vrai `RpcMethodHandler` (via le helper grpc officiel) dont le
    comportement lève `exc`, le fait passer par l'intercepteur, et renvoie le
    `unary_unary` enveloppé — prêt à être appelé avec (request, context)."""
    interceptor = ErrorHandlingInterceptor()

    def behavior(request: Any, context: grpc.ServicerContext) -> Any:
        raise exc

    handler: grpc.RpcMethodHandler[Any, Any] = grpc.unary_unary_rpc_method_handler(behavior)
    continuation = MagicMock(return_value=handler)
    wrapped = interceptor.intercept_service(continuation, MagicMock(method="/Auth/X"))
    assert wrapped is not None
    return wrapped.unary_unary


class ErrorHandlingInterceptorTests(SimpleTestCase):
    def setUp(self) -> None:
        self.interceptor = ErrorHandlingInterceptor()
        self.context = MagicMock(spec=grpc.ServicerContext)

    def test_authentication_error_maps_to_unauthenticated(self) -> None:
        behavior = _wrapped_behavior(AuthenticationError("Identifiants invalides"))
        with self.assertRaises(AuthenticationError):
            behavior(request=None, context=self.context)
        self.context.abort.assert_called_once_with(grpc.StatusCode.UNAUTHENTICATED, "Identifiants invalides")

    def test_object_does_not_exist_maps_to_not_found(self) -> None:
        behavior = _wrapped_behavior(ObjectDoesNotExist("Utilisateur introuvable"))
        with self.assertRaises(ObjectDoesNotExist):
            behavior(request=None, context=self.context)
        self.assertEqual(self.context.abort.call_args[0][0], grpc.StatusCode.NOT_FOUND)

    def test_throttle_error_maps_to_resource_exhausted(self) -> None:
        behavior = _wrapped_behavior(ThrottleError("Trop de demandes, réessayez dans au plus 60 secondes"))
        with self.assertRaises(ThrottleError):
            behavior(request=None, context=self.context)
        self.context.abort.assert_called_once_with(
            grpc.StatusCode.RESOURCE_EXHAUSTED, "Trop de demandes, réessayez dans au plus 60 secondes"
        )

    def test_value_error_maps_to_invalid_argument(self) -> None:
        behavior = _wrapped_behavior(ValueError("Numéro de téléphone invalide"))
        with self.assertRaises(ValueError):
            behavior(request=None, context=self.context)
        self.context.abort.assert_called_once_with(grpc.StatusCode.INVALID_ARGUMENT, "Numéro de téléphone invalide")

    def test_integrity_error_maps_to_already_exists_with_generic_message(self) -> None:
        behavior = _wrapped_behavior(IntegrityError("duplicate key value violates unique constraint"))
        with self.assertRaises(IntegrityError):
            behavior(request=None, context=self.context)
        self.context.abort.assert_called_once_with(grpc.StatusCode.ALREADY_EXISTS, "Cette ressource existe déjà")

    def test_email_delivery_error_maps_to_unavailable_with_generic_message(self) -> None:
        behavior = _wrapped_behavior(EmailDeliveryError("Brevo a renvoyé 500: ..."))
        with self.assertRaises(EmailDeliveryError):
            behavior(request=None, context=self.context)
        self.context.abort.assert_called_once_with(
            grpc.StatusCode.UNAVAILABLE, "Échec de l'envoi de l'e-mail, réessayez plus tard"
        )

    def test_whatsapp_delivery_error_maps_to_unavailable_with_generic_message(self) -> None:
        behavior = _wrapped_behavior(WhatsAppDeliveryError("Service WhatsApp inaccessible"))
        with self.assertRaises(WhatsAppDeliveryError):
            behavior(request=None, context=self.context)
        self.context.abort.assert_called_once_with(
            grpc.StatusCode.UNAVAILABLE, "Échec de l'envoi WhatsApp, réessayez plus tard"
        )

    def test_unknown_exception_propagates_unchanged(self) -> None:
        # RuntimeError n'est pas dans le mapping → remonte sans modification.
        behavior = _wrapped_behavior(RuntimeError("boom"))
        with self.assertRaises(RuntimeError):
            behavior(request=None, context=self.context)
        self.context.abort.assert_not_called()

    def test_successful_call_passes_through(self) -> None:
        handler: grpc.RpcMethodHandler[Any, Any] = grpc.unary_unary_rpc_method_handler(lambda request, context: "ok")
        wrapped = self.interceptor.intercept_service(MagicMock(return_value=handler), MagicMock(method="/Auth/X"))
        assert wrapped is not None and wrapped.unary_unary is not None
        self.assertEqual(wrapped.unary_unary(None, self.context), "ok")

    def test_non_unary_unary_handler_passes_through(self) -> None:
        # Un handler de streaming (unary_stream) n'a pas de `unary_unary` :
        # l'intercepteur doit le laisser passer inchangé.
        handler: grpc.RpcMethodHandler[Any, Any] = grpc.unary_stream_rpc_method_handler(
            lambda request, context: iter(())
        )
        wrapped = self.interceptor.intercept_service(MagicMock(return_value=handler), MagicMock(method="/Auth/X"))
        assert wrapped is not None
        self.assertIsNone(wrapped.unary_unary)
