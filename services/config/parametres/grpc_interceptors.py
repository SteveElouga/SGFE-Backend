from __future__ import annotations

import logging
from typing import Any, Callable

import grpc
from django.core.exceptions import ObjectDoesNotExist

logger = logging.getLogger(__name__)

_STATUS_BY_EXCEPTION = ((ObjectDoesNotExist, grpc.StatusCode.NOT_FOUND, None),)


def _abort_for(exc: Exception, context: grpc.ServicerContext, handler_call_details: grpc.HandlerCallDetails) -> None:
    for exc_type, status_code, message in _STATUS_BY_EXCEPTION:
        if isinstance(exc, exc_type):
            context.abort(status_code, message or str(exc))
            return
    method = getattr(handler_call_details, "method", "?")
    logger.exception("Exception non gérée dans %s", method)


class ErrorHandlingInterceptor(grpc.ServerInterceptor):
    """Convertit les exceptions Django en codes gRPC appropriés."""

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
