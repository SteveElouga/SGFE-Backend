import asyncio

import grpc
from django.test import SimpleTestCase

from schema.extensions import GrpcErrorExtension
from schema.tests.test_auth import FakeRpcError


class GrpcErrorExtensionTests(SimpleTestCase):
    def setUp(self):
        self.extension = GrpcErrorExtension(execution_context=None)

    def test_sync_resolver_passes_through_on_success(self):
        result = self.extension.resolve(lambda root, info: "ok", root=None, info=None)
        self.assertEqual(result, "ok")

    def test_sync_resolver_rpc_error_translated(self):
        def _next(root, info):
            raise FakeRpcError("Identifiants invalides")

        with self.assertRaisesMessage(Exception, "Identifiants invalides"):
            self.extension.resolve(_next, root=None, info=None)

    def test_async_resolver_passes_through_on_success(self):
        async def _next(root, info):
            return "ok"

        result = asyncio.run(self.extension.resolve(_next, root=None, info=None))
        self.assertEqual(result, "ok")

    def test_async_resolver_rpc_error_translated(self):
        async def _next(root, info):
            raise FakeRpcError("Token révoqué")

        with self.assertRaisesMessage(Exception, "Token révoqué"):
            asyncio.run(self.extension.resolve(_next, root=None, info=None))

    def test_unknown_status_without_details_uses_generic_message(self):
        def _next(root, info):
            raise FakeRpcError("", status_code=grpc.StatusCode.INTERNAL)

        with self.assertRaisesMessage(Exception, "Erreur du service distant"):
            self.extension.resolve(_next, root=None, info=None)
