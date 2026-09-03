import asyncio
from typing import Any
from unittest.mock import MagicMock

import grpc
from django.test import SimpleTestCase

from schema.extensions import GrpcErrorExtension
from schema.tests.test_auth import FakeRpcError


class GrpcErrorExtensionTests(SimpleTestCase):
    def setUp(self) -> None:
        self.extension = GrpcErrorExtension(execution_context=None)

    def test_sync_resolver_passes_through_on_success(self) -> None:
        result = self.extension.resolve(lambda root, info: "ok", root=None, info=MagicMock())
        self.assertEqual(result, "ok")

    def test_sync_resolver_rpc_error_translated(self) -> None:
        def _next(root: Any, info: Any) -> Any:
            raise FakeRpcError("Identifiants invalides")

        with self.assertRaisesMessage(Exception, "Identifiants invalides"):
            self.extension.resolve(_next, root=None, info=MagicMock())

    def test_async_resolver_passes_through_on_success(self) -> None:
        async def _next(root: Any, info: Any) -> Any:
            return "ok"

        # `resolve()` est statiquement `Awaitable[object] | object` (signature
        # de base strawberry) ; ici `_next` est asynchrone donc l'appel rend
        # toujours une vraie coroutine à l'exécution.
        result: object = asyncio.run(self.extension.resolve(_next, root=None, info=MagicMock()))  # type: ignore[arg-type]
        self.assertEqual(result, "ok")

    def test_async_resolver_rpc_error_translated(self) -> None:
        async def _next(root: Any, info: Any) -> Any:
            raise FakeRpcError("Token révoqué")

        with self.assertRaisesMessage(Exception, "Token révoqué"):
            asyncio.run(self.extension.resolve(_next, root=None, info=MagicMock()))  # type: ignore[arg-type]

    def test_unknown_status_without_details_uses_generic_message(self) -> None:
        def _next(root: Any, info: Any) -> Any:
            raise FakeRpcError("", status_code=grpc.StatusCode.INTERNAL)

        with self.assertRaisesMessage(Exception, "Erreur du service distant"):
            self.extension.resolve(_next, root=None, info=MagicMock())
