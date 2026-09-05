"""Tests de l'extraction du token — header HTTP et connectionParams WebSocket."""

from unittest import TestCase
from unittest.mock import MagicMock, patch

import grpc

from schema.context import AuthError, _token_from_connection_params, require_auth, require_role
from schema.identity_context import get_identity, reset_identity


def _info(connection_params: dict[str, str], header: str = "") -> MagicMock:
    """Fabrique un `info` GraphQL : requête sans header d'auth + connectionParams."""
    request = MagicMock()
    request.headers.get.return_value = header  # extract_token lit .headers.get("Authorization", "")
    info = MagicMock()
    info.context = {"request": request, "connection_params": connection_params}
    return info


class ConnectionParamsAuthTests(TestCase):
    def tearDown(self) -> None:
        # `require_auth` pose l'identité dans un ContextVar global (voir
        # `identity_context.py`) : la réinitialiser évite qu'une identité
        # posée ici (parfois avec des attributs `MagicMock` non typés `str`)
        # ne fuite vers un test suivant qui utiliserait le canal gRPC réel.
        reset_identity()

    @patch("schema.context.auth_client")
    def test_require_auth_lit_le_token_des_connection_params(self, mock_auth_client: MagicMock) -> None:
        """Sur une subscription WS (pas de header), le JWT est lu dans connectionParams."""
        mock_auth_client.validate_token.return_value = MagicMock(role="ADMIN", user_id="u-1", username="alice")
        info = _info({"Authorization": "Bearer tok-123"})

        require_auth(info)

        mock_auth_client.validate_token.assert_called_once_with("tok-123")

    def test_token_from_connection_params_tolerant_aux_cles(self) -> None:
        self.assertEqual(_token_from_connection_params(_info({"authToken": "raw-token"})), "raw-token")
        self.assertEqual(_token_from_connection_params(_info({"token": "raw-token"})), "raw-token")
        self.assertEqual(_token_from_connection_params(_info({"authorization": "Bearer x"})), "x")

    def test_require_auth_sans_token_leve_autherror(self) -> None:
        with self.assertRaises(AuthError):
            require_auth(_info({}))

    @patch("schema.context.auth_client")
    def test_require_auth_pose_l_identite_courante(self, mock_auth_client: MagicMock) -> None:
        """Voir AUDIT_SGFE.md §10.7 : `require_auth` pose l'identité juste après `validate_token`."""
        mock_auth_client.validate_token.return_value = MagicMock(user_id="u-1", username="alice", role="COMPTABLE")

        require_auth(_info({"Authorization": "Bearer tok-123"}))

        identity = get_identity()
        assert identity is not None
        self.assertEqual((identity.user_id, identity.username, identity.role), ("u-1", "alice", "COMPTABLE"))


class SecurityLoggingTests(TestCase):
    """Journalisation de sécurité (voir AUDIT_SGFE.md §J) : échecs de
    validation de jeton et refus de rôle, sur le logger dédié `security`."""

    def tearDown(self) -> None:
        reset_identity()

    @patch("schema.context.auth_client")
    def test_echec_de_validation_de_jeton_journalise_en_securite(self, mock_auth_client: MagicMock) -> None:
        mock_auth_client.validate_token.side_effect = grpc.RpcError("jeton invalide")

        with self.assertLogs("security", level="WARNING") as journaux:
            with self.assertRaises(grpc.RpcError):
                require_auth(_info({"Authorization": "Bearer tok-invalide"}))

        self.assertTrue(any("validation de jeton" in ligne for ligne in journaux.output))

    @patch("schema.context.auth_client")
    def test_refus_de_role_journalise_en_securite(self, mock_auth_client: MagicMock) -> None:
        mock_auth_client.validate_token.return_value = MagicMock(user_id="u-1", username="bob", role="AGENT")

        with self.assertLogs("security", level="WARNING") as journaux:
            with self.assertRaises(AuthError):
                require_role(_info({"Authorization": "Bearer tok-123"}), "ADMIN", "COMPTABLE")

        self.assertTrue(any("Accès refusé" in ligne for ligne in journaux.output))

    @patch("schema.context.auth_client")
    def test_role_suffisant_ne_journalise_rien_en_securite(self, mock_auth_client: MagicMock) -> None:
        mock_auth_client.validate_token.return_value = MagicMock(user_id="u-1", username="admin", role="ADMIN")

        # `assertNoLogs` n'existe qu'à partir de Python 3.10 (disponible ici) —
        # aucun message ne doit remonter sur le logger `security`.
        with self.assertNoLogs("security", level="WARNING"):
            require_role(_info({"Authorization": "Bearer tok-123"}), "ADMIN")
        mock_auth_client.enregistrer_evenement_securite.assert_not_called()


class CentralisationEvenementSecuriteTests(TestCase):
    """Centralisation des événements de sécurité dans l'`AuditLog` de Auth
    (voir AUDIT_SGFE.md §J) — best-effort, jamais bloquant."""

    def tearDown(self) -> None:
        reset_identity()

    @patch("schema.context.auth_client")
    def test_echec_de_validation_de_jeton_relaie_vers_auth_client(self, mock_auth_client: MagicMock) -> None:
        mock_auth_client.validate_token.side_effect = grpc.RpcError("jeton invalide")

        with self.assertRaises(grpc.RpcError):
            require_auth(_info({"Authorization": "Bearer tok-invalide"}))

        mock_auth_client.enregistrer_evenement_securite.assert_called_once()
        kwargs = mock_auth_client.enregistrer_evenement_securite.call_args.kwargs
        self.assertEqual(kwargs["type_evenement"], "TOKEN_INVALIDE")
        self.assertEqual(kwargs["acteur_id"], "")

    @patch("schema.context.auth_client")
    def test_refus_de_role_relaie_vers_auth_client_avec_l_acteur(self, mock_auth_client: MagicMock) -> None:
        mock_auth_client.validate_token.return_value = MagicMock(user_id="u-1", username="bob", role="AGENT")

        with self.assertRaises(AuthError):
            require_role(_info({"Authorization": "Bearer tok-123"}), "ADMIN", "COMPTABLE")

        mock_auth_client.enregistrer_evenement_securite.assert_called_once()
        kwargs = mock_auth_client.enregistrer_evenement_securite.call_args.kwargs
        self.assertEqual(kwargs["type_evenement"], "ROLE_REFUSE")
        self.assertEqual((kwargs["acteur_id"], kwargs["acteur_nom"], kwargs["acteur_role"]), ("u-1", "bob", "AGENT"))

    @patch("schema.context.auth_client")
    def test_echec_de_la_centralisation_ne_bloque_jamais_la_requete(self, mock_auth_client: MagicMock) -> None:
        """Auth-service indisponible pour ce RPC précis : `require_role` doit
        quand même lever `AuthError` (le refus de rôle reste effectif), pas
        une exception liée à l'échec de la centralisation elle-même."""
        mock_auth_client.validate_token.return_value = MagicMock(user_id="u-1", username="bob", role="AGENT")
        mock_auth_client.enregistrer_evenement_securite.side_effect = RuntimeError("auth-service indisponible")

        with self.assertLogs("security", level="WARNING"):
            with self.assertRaises(AuthError):
                require_role(_info({"Authorization": "Bearer tok-123"}), "ADMIN")

    @patch("schema.context.auth_client")
    def test_echec_de_la_centralisation_pour_jeton_invalide_ne_bloque_jamais(self, mock_auth_client: MagicMock) -> None:
        mock_auth_client.validate_token.side_effect = grpc.RpcError("jeton invalide")
        mock_auth_client.enregistrer_evenement_securite.side_effect = RuntimeError("auth-service indisponible")

        # L'exception d'origine (grpc.RpcError) doit toujours être celle qui
        # remonte — pas la RuntimeError de l'appel de centralisation échoué.
        with self.assertRaises(grpc.RpcError):
            require_auth(_info({"Authorization": "Bearer tok-invalide"}))
