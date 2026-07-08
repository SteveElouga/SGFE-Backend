"""Tests de l'ErrorHandlingInterceptor du Facturation Service (mapping exception -> code gRPC)."""

from unittest.mock import MagicMock

import grpc
from django.core.exceptions import ObjectDoesNotExist, ValidationError
from django.test import SimpleTestCase

from factures.exceptions import PreconditionError
from factures.grpc_interceptors import _abort_for


class AbortForTests(SimpleTestCase):
    def _code(self, exc: Exception):
        ctx = MagicMock(spec=grpc.ServicerContext)
        _abort_for(exc, ctx, MagicMock(method="/Facturation/X"))
        return ctx.abort.call_args[0][0] if ctx.abort.called else None, ctx

    def test_precondition_error_mappe_failed_precondition(self) -> None:
        # Priorité : PreconditionError est une sous-classe de ValidationError mais
        # doit être mappée en FAILED_PRECONDITION, pas INVALID_ARGUMENT.
        code, _ = self._code(PreconditionError("aucun tarif actif"))
        self.assertEqual(code, grpc.StatusCode.FAILED_PRECONDITION)

    def test_validation_error_mappe_invalid_argument(self) -> None:
        code, _ = self._code(ValidationError("prix invalide"))
        self.assertEqual(code, grpc.StatusCode.INVALID_ARGUMENT)

    def test_object_does_not_exist_mappe_not_found(self) -> None:
        code, _ = self._code(ObjectDoesNotExist("facture introuvable"))
        self.assertEqual(code, grpc.StatusCode.NOT_FOUND)

    def test_rpc_error_mappe_unavailable(self) -> None:
        code, _ = self._code(grpc.RpcError())
        self.assertEqual(code, grpc.StatusCode.UNAVAILABLE)

    def test_file_not_found_mappe_internal(self) -> None:
        code, _ = self._code(FileNotFoundError("pdf manquant"))
        self.assertEqual(code, grpc.StatusCode.INTERNAL)

    def test_exception_non_mappee_ne_pas_abort(self) -> None:
        code, ctx = self._code(RuntimeError("boom"))
        self.assertIsNone(code)
        ctx.abort.assert_not_called()
