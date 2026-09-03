"""Tests de l'ErrorHandlingInterceptor du Paiement Service (mapping exception -> code gRPC)."""

from __future__ import annotations

from typing import Any
from unittest.mock import MagicMock

import grpc
from django.core.exceptions import ObjectDoesNotExist, ValidationError
from django.test import SimpleTestCase

from paiements.grpc_interceptors import ErrorHandlingInterceptor, _abort_for


class AbortForTests(SimpleTestCase):
    def _abort(self, exc: Exception) -> MagicMock:
        ctx = MagicMock(spec=grpc.ServicerContext)
        _abort_for(exc, ctx, MagicMock(method="/Paiement/X"))
        return ctx

    def test_object_does_not_exist_mappe_not_found(self) -> None:
        ctx = self._abort(ObjectDoesNotExist("introuvable"))
        ctx.abort.assert_called_once()
        self.assertEqual(ctx.abort.call_args[0][0], grpc.StatusCode.NOT_FOUND)

    def test_validation_error_mappe_invalid_argument(self) -> None:
        ctx = self._abort(ValidationError("montant invalide"))
        self.assertEqual(ctx.abort.call_args[0][0], grpc.StatusCode.INVALID_ARGUMENT)

    def test_value_error_mappe_invalid_argument(self) -> None:
        ctx = self._abort(ValueError("date invalide"))
        self.assertEqual(ctx.abort.call_args[0][0], grpc.StatusCode.INVALID_ARGUMENT)

    def test_exception_non_mappee_ne_pas_abort(self) -> None:
        # Une exception inattendue n'est pas mappée : elle sera propagée (-> UNKNOWN).
        ctx = self._abort(RuntimeError("boom"))
        ctx.abort.assert_not_called()


class InterceptorBehaviorTests(SimpleTestCase):
    """Vérifie le comportement de bout en bout du wrapper unary_unary."""

    def _wrap(self, exc: Exception) -> tuple[grpc.RpcMethodHandler[Any, Any], MagicMock]:
        interceptor = ErrorHandlingInterceptor()

        def behavior(request: Any, context: grpc.ServicerContext) -> Any:
            raise exc

        handler: grpc.RpcMethodHandler[Any, Any] = grpc.unary_unary_rpc_method_handler(behavior)
        continuation = MagicMock(return_value=handler)
        wrapped = interceptor.intercept_service(continuation, MagicMock(method="/Paiement/X"))
        assert wrapped is not None  # `continuation` renvoie toujours un handler ici
        ctx = MagicMock(spec=grpc.ServicerContext)
        ctx.abort.side_effect = RuntimeError("aborted")  # le vrai context.abort lève
        return wrapped, ctx

    def test_mappe_les_exceptions_connues(self) -> None:
        wrapped, ctx = self._wrap(ValidationError("bad"))
        assert wrapped.unary_unary is not None
        with self.assertRaises(Exception):
            wrapped.unary_unary(MagicMock(), ctx)
        ctx.abort.assert_called_once()
        self.assertEqual(ctx.abort.call_args[0][0], grpc.StatusCode.INVALID_ARGUMENT)

    def test_propage_les_exceptions_inattendues(self) -> None:
        wrapped, ctx = self._wrap(RuntimeError("boom"))
        assert wrapped.unary_unary is not None
        with self.assertRaises(RuntimeError):
            wrapped.unary_unary(MagicMock(), ctx)
        ctx.abort.assert_not_called()
