"""Tests de `IdentityInterceptor` (voir AUDIT_SGFE.md §10.7).

Métadonnées présentes ⇒ `get_caller()` renvoie l'identité propagée par la
gateway ; absentes ⇒ `get_caller()` renvoie une identité vide (jamais `None`).
"""

from __future__ import annotations

from typing import Any
from unittest.mock import MagicMock

import grpc
from django.test import SimpleTestCase

from paiements.grpc_interceptors import CallerIdentity, IdentityInterceptor, get_caller


class IdentityInterceptorTests(SimpleTestCase):
    def _wrap(self, metadata: tuple[tuple[str, str], ...]) -> Any:
        interceptor = IdentityInterceptor()
        vu: dict[str, CallerIdentity] = {}

        def behavior(request: Any, context: grpc.ServicerContext) -> str:
            vu["caller"] = get_caller()
            return "ok"

        handler: grpc.RpcMethodHandler[Any, Any] = grpc.unary_unary_rpc_method_handler(behavior)
        continuation = MagicMock(return_value=handler)
        details = MagicMock(method="/Paiements/X", invocation_metadata=metadata)
        wrapped = interceptor.intercept_service(continuation, details)
        assert wrapped is not None and wrapped.unary_unary is not None
        wrapped.unary_unary(MagicMock(), MagicMock(spec=grpc.ServicerContext))
        return vu["caller"]

    def test_metadonnees_presentes_get_caller_renvoie_l_identite(self) -> None:
        caller = self._wrap(
            (
                ("x-user-id", "u-1"),
                ("x-user-name", "alice"),
                ("x-user-role", "COMPTABLE"),
                ("x-request-id", "req-1"),
            )
        )
        self.assertEqual(caller.user_id, "u-1")
        self.assertEqual(caller.username, "alice")
        self.assertEqual(caller.role, "COMPTABLE")
        self.assertEqual(caller.request_id, "req-1")
        self.assertFalse(caller.is_anonyme)

    def test_metadonnees_absentes_get_caller_renvoie_une_identite_vide(self) -> None:
        caller = self._wrap(())
        self.assertEqual(caller, CallerIdentity())
        self.assertTrue(caller.is_anonyme)

    def test_get_caller_hors_appel_renvoie_une_identite_vide(self) -> None:
        # Aucun appel en cours (contexte par défaut du ContextVar) : pas de
        # `None`, une identité vide — comme documenté sur `get_caller`.
        self.assertEqual(get_caller(), CallerIdentity())

    def test_contextvar_remis_a_zero_apres_l_appel(self) -> None:
        self._wrap((("x-user-id", "u-1"), ("x-user-name", "alice"), ("x-user-role", "ADMIN")))
        # Une fois l'appel terminé, le ContextVar est remis à son état
        # d'avant : un appel suivant sans métadonnées ne doit pas hériter
        # de l'identité du précédent.
        self.assertEqual(get_caller(), CallerIdentity())

    def test_handler_none_est_transmis_tel_quel(self) -> None:
        interceptor = IdentityInterceptor()
        continuation = MagicMock(return_value=None)
        resultat = interceptor.intercept_service(continuation, MagicMock(invocation_metadata=()))
        self.assertIsNone(resultat)
