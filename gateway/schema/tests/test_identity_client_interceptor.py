"""Tests de `IdentityClientInterceptor` (schema/grpc_clients.py).

Voir AUDIT_SGFE.md §10.7 : contextvar peuplé ⇒ métadonnées x-user-*/x-request-id
présentes ; contextvar vide (appel anonyme) ⇒ aucune métadonnée ajoutée.
"""

from unittest.mock import MagicMock

from django.test import SimpleTestCase

from schema.grpc_clients import IdentityClientInterceptor
from schema.identity_context import reset_identity, set_identity


class IdentityClientInterceptorTests(SimpleTestCase):
    def tearDown(self) -> None:
        reset_identity()

    def _details(self, metadata: object = None) -> MagicMock:
        return MagicMock(method="/x/Y", timeout=None, metadata=metadata, credentials=None)

    def test_contextvar_peuple_ajoute_les_metadonnees(self) -> None:
        set_identity(user_id="u-1", username="alice", role="COMPTABLE")
        interceptor = IdentityClientInterceptor()
        suite = MagicMock(return_value="reponse")

        interceptor.intercept_unary_unary(suite, self._details(), "requete")

        envoyes = dict(suite.call_args[0][0].metadata)
        self.assertEqual(envoyes["x-user-id"], "u-1")
        self.assertEqual(envoyes["x-user-name"], "alice")
        self.assertEqual(envoyes["x-user-role"], "COMPTABLE")
        self.assertTrue(envoyes["x-request-id"])

    def test_contextvar_vide_appel_anonyme_aucune_metadonnee(self) -> None:
        reset_identity()
        interceptor = IdentityClientInterceptor()
        suite = MagicMock(return_value="reponse")
        details = self._details()

        interceptor.intercept_unary_unary(suite, details, "requete")

        # Aucune enveloppe supplémentaire : les `client_call_details` d'origine
        # sont transmis tels quels (pas de _DetailsAppel reconstruit).
        suite.assert_called_once_with(details, "requete")

    def test_preserve_la_metadonnee_existante(self) -> None:
        set_identity(user_id="u-1", username="alice", role="ADMIN")
        interceptor = IdentityClientInterceptor()
        suite = MagicMock(return_value="reponse")
        details = self._details(metadata=(("x-internal-key", "cle"),))

        interceptor.intercept_unary_unary(suite, details, "requete")

        envoyes = dict(suite.call_args[0][0].metadata)
        self.assertEqual(envoyes["x-internal-key"], "cle")
        self.assertEqual(envoyes["x-user-id"], "u-1")

    def test_meme_requete_meme_request_id_sur_deux_appels(self) -> None:
        set_identity(user_id="u-1", username="alice", role="ADMIN")
        interceptor = IdentityClientInterceptor()
        suite = MagicMock(return_value="reponse")

        interceptor.intercept_unary_unary(suite, self._details(), "requete-1")
        premier = dict(suite.call_args[0][0].metadata)["x-request-id"]
        interceptor.intercept_unary_unary(suite, self._details(), "requete-2")
        second = dict(suite.call_args[0][0].metadata)["x-request-id"]

        self.assertEqual(premier, second)
