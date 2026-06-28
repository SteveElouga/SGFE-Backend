import grpc
from django.core.exceptions import ObjectDoesNotExist
from django.db import IntegrityError
from django.test import SimpleTestCase

from abonnes.grpc_interceptors import ErrorHandlingInterceptor
from abonnes.services import ValidationError


class FakeHandler:
    def __init__(self, behavior):
        self.unary_unary = behavior
        self.request_deserializer = None
        self.response_serializer = None


class AbortCalled(Exception):
    def __init__(self, code, details):
        self.code = code
        self.details = details
        super().__init__(details)


class FakeContext:
    def abort(self, code, details):
        raise AbortCalled(code, details)


def continuation_raising(exc: Exception):
    def behavior(request, context):
        raise exc

    return lambda handler_call_details: FakeHandler(behavior)


class ErrorHandlingInterceptorTests(SimpleTestCase):
    def setUp(self):
        self.interceptor = ErrorHandlingInterceptor()
        self.context = FakeContext()

    def _wrapped_behavior(self, exc: Exception):
        handler = self.interceptor.intercept_service(continuation_raising(exc), handler_call_details=None)
        return handler.unary_unary

    def test_validation_error_maps_to_invalid_argument(self):
        behavior = self._wrapped_behavior(ValidationError("Index invalide"))
        with self.assertRaises(AbortCalled) as cm:
            behavior(request=None, context=self.context)
        self.assertEqual(cm.exception.code, grpc.StatusCode.INVALID_ARGUMENT)
        self.assertEqual(cm.exception.details, "Index invalide")

    def test_object_does_not_exist_maps_to_not_found(self):
        behavior = self._wrapped_behavior(ObjectDoesNotExist("Abonné introuvable"))
        with self.assertRaises(AbortCalled) as cm:
            behavior(request=None, context=self.context)
        self.assertEqual(cm.exception.code, grpc.StatusCode.NOT_FOUND)

    def test_integrity_error_maps_to_already_exists_with_generic_message(self):
        behavior = self._wrapped_behavior(IntegrityError("duplicate key value violates unique constraint"))
        with self.assertRaises(AbortCalled) as cm:
            behavior(request=None, context=self.context)
        self.assertEqual(cm.exception.code, grpc.StatusCode.ALREADY_EXISTS)
        self.assertEqual(cm.exception.details, "Cette ressource existe déjà")

    def test_unknown_exception_propagates_unchanged(self):
        behavior = self._wrapped_behavior(ValueError("boom"))
        with self.assertRaises(ValueError):
            behavior(request=None, context=self.context)

    def test_successful_call_passes_through(self):
        handler = self.interceptor.intercept_service(
            lambda handler_call_details: FakeHandler(lambda request, context: "ok"),
            handler_call_details=None,
        )
        self.assertEqual(handler.unary_unary(request=None, context=self.context), "ok")

    def test_non_unary_unary_handler_passes_through(self):
        handler = self.interceptor.intercept_service(
            lambda handler_call_details: FakeHandler(None), handler_call_details=None
        )
        self.assertIsNone(handler.unary_unary)
