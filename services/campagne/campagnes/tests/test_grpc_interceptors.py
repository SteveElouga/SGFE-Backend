"""Tests de l'ErrorHandlingInterceptor du Campagne Service (mapping exception -> code gRPC)."""

from unittest.mock import MagicMock

import grpc
from django.core.exceptions import ObjectDoesNotExist, ValidationError
from django.test import SimpleTestCase

from campagnes.grpc_interceptors import ErrorHandlingInterceptor, _abort_for


class AbortForTests(SimpleTestCase):
    def _abort(self, exc: Exception) -> MagicMock:
        ctx = MagicMock(spec=grpc.ServicerContext)
        _abort_for(exc, ctx, MagicMock(method="/Campagne/X"))
        return ctx

    def test_object_does_not_exist_mappe_not_found(self) -> None:
        ctx = self._abort(ObjectDoesNotExist("introuvable"))
        ctx.abort.assert_called_once()
        self.assertEqual(ctx.abort.call_args[0][0], grpc.StatusCode.NOT_FOUND)

    def test_validation_error_mappe_invalid_argument(self) -> None:
        ctx = self._abort(ValidationError("invalide"))
        self.assertEqual(ctx.abort.call_args[0][0], grpc.StatusCode.INVALID_ARGUMENT)

    def test_exception_non_mappee_ne_pas_abort(self) -> None:
        ctx = self._abort(RuntimeError("boom"))
        ctx.abort.assert_not_called()


class InterceptorBehaviorTests(SimpleTestCase):
    def _wrap(self, exc: Exception):
        interceptor = ErrorHandlingInterceptor()

        def behavior(request, context):
            raise exc

        handler = grpc.unary_unary_rpc_method_handler(behavior)
        continuation = MagicMock(return_value=handler)
        wrapped = interceptor.intercept_service(continuation, MagicMock(method="/Campagne/X"))
        ctx = MagicMock(spec=grpc.ServicerContext)
        ctx.abort.side_effect = RuntimeError("aborted")
        return wrapped, ctx

    def test_mappe_les_exceptions_connues(self) -> None:
        wrapped, ctx = self._wrap(ObjectDoesNotExist("x"))
        with self.assertRaises(Exception):
            wrapped.unary_unary(MagicMock(), ctx)
        ctx.abort.assert_called_once()
        self.assertEqual(ctx.abort.call_args[0][0], grpc.StatusCode.NOT_FOUND)

    def test_propage_les_exceptions_inattendues(self) -> None:
        wrapped, ctx = self._wrap(RuntimeError("boom"))
        with self.assertRaises(RuntimeError):
            wrapped.unary_unary(MagicMock(), ctx)
        ctx.abort.assert_not_called()
