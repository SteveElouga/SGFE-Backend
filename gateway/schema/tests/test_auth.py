from unittest.mock import Mock, patch

import grpc
from django.test import SimpleTestCase

from schema.grpc_clients import auth_client
from schema.schema import schema


class FakeRpcError(grpc.RpcError):
    """Simule une grpc.RpcError côté client, avec code()/details() comme un vrai appel échoué."""

    def __init__(self, message: str, status_code: grpc.StatusCode = grpc.StatusCode.UNKNOWN) -> None:
        self._message = message
        self._status_code = status_code
        super().__init__(message)

    def details(self) -> str:
        return self._message

    def code(self) -> grpc.StatusCode:
        return self._status_code


class FakeRequest:
    def __init__(self, headers: dict | None = None) -> None:
        self.headers = headers or {}


def context(token: str | None = None) -> dict:
    headers = {"Authorization": f"Bearer {token}"} if token else {}
    return {"request": FakeRequest(headers=headers)}


def make_user_response(user_id="user-1", username="comptable1", role="COMPTABLE", is_active=True):
    return Mock(
        user_id=user_id, username=username, email=f"{username}@example.com",
        role=role, is_active=is_active, created_at="2024-01-01T00:00:00",
    )


class LoginMutationTests(SimpleTestCase):
    def test_login_success_returns_auth_payload(self):
        with patch.multiple(
            auth_client,
            login=Mock(return_value=Mock(access_token="access-1", refresh_token="refresh-1", expires_in=86400)),
            validate_token=Mock(return_value=Mock(user_id="user-1", role="COMPTABLE")),
            get_user=Mock(return_value=make_user_response()),
        ):
            result = schema.execute_sync(
                'mutation { login(username: "comptable1", password: "secret123") '
                "{ accessToken refreshToken expiresIn user { username role } } }",
                context_value=context(),
            )

        self.assertIsNone(result.errors)
        self.assertEqual(result.data["login"]["accessToken"], "access-1")
        self.assertEqual(result.data["login"]["user"]["username"], "comptable1")
        self.assertEqual(result.data["login"]["user"]["role"], "COMPTABLE")

    def test_login_failure_returns_graphql_error_with_grpc_details(self):
        with patch.object(auth_client, "login", side_effect=FakeRpcError("Identifiants invalides")):
            result = schema.execute_sync(
                'mutation { login(username: "comptable1", password: "wrong") { accessToken } }',
                context_value=context(),
            )

        self.assertIsNotNone(result.errors)
        self.assertIn("Identifiants invalides", str(result.errors))

    def test_login_failure_without_details_falls_back_to_status_message(self):
        with patch.object(
            auth_client,
            "login",
            side_effect=FakeRpcError("", status_code=grpc.StatusCode.UNAUTHENTICATED),
        ):
            result = schema.execute_sync(
                'mutation { login(username: "comptable1", password: "wrong") { accessToken } }',
                context_value=context(),
            )

        self.assertIsNotNone(result.errors)
        self.assertIn("Authentification requise ou invalide", str(result.errors))


class RefreshTokenMutationTests(SimpleTestCase):
    def test_refresh_token_success(self):
        with patch.multiple(
            auth_client,
            refresh_token=Mock(return_value=Mock(access_token="access-2", refresh_token="refresh-2", expires_in=86400)),
            validate_token=Mock(return_value=Mock(user_id="user-1", role="COMPTABLE")),
            get_user=Mock(return_value=make_user_response()),
        ):
            result = schema.execute_sync(
                'mutation { refreshToken(token: "refresh-1") { accessToken } }',
                context_value=context(),
            )

        self.assertIsNone(result.errors)
        self.assertEqual(result.data["refreshToken"]["accessToken"], "access-2")

    def test_refresh_token_failure_returns_graphql_error(self):
        with patch.object(auth_client, "refresh_token", side_effect=FakeRpcError("token invalide")):
            result = schema.execute_sync(
                'mutation { refreshToken(token: "invalide") { accessToken } }',
                context_value=context(),
            )

        self.assertIsNotNone(result.errors)


class LogoutMutationTests(SimpleTestCase):
    def test_logout_success(self):
        with patch.object(auth_client, "logout", return_value=Mock(success=True)):
            result = schema.execute_sync("mutation { logout }", context_value=context(token="access-1"))

        self.assertIsNone(result.errors)
        self.assertTrue(result.data["logout"])

    def test_logout_without_token_returns_error(self):
        result = schema.execute_sync("mutation { logout }", context_value=context())

        self.assertIsNotNone(result.errors)
        self.assertIn("Authentification requise", str(result.errors))


class MeQueryTests(SimpleTestCase):
    def test_me_requires_auth(self):
        result = schema.execute_sync("query { me { username } }", context_value=context())

        self.assertIsNotNone(result.errors)
        self.assertIn("Authentification requise", str(result.errors))

    def test_me_returns_current_user(self):
        with patch.multiple(
            auth_client,
            validate_token=Mock(return_value=Mock(user_id="user-1", role="AGENT")),
            get_user=Mock(return_value=make_user_response(username="agent1", role="AGENT")),
        ):
            result = schema.execute_sync("query { me { username role } }", context_value=context(token="access-1"))

        self.assertIsNone(result.errors)
        self.assertEqual(result.data["me"]["username"], "agent1")
        self.assertEqual(result.data["me"]["role"], "AGENT")


class UserAdminMutationTests(SimpleTestCase):
    def test_create_user_requires_admin_role(self):
        with patch.object(auth_client, "validate_token", return_value=Mock(user_id="user-1", role="AGENT")):
            result = schema.execute_sync(
                'mutation { createUser(username: "x", email: "x@example.com", password: "secret123", role: AGENT) '
                "{ username } }",
                context_value=context(token="access-1"),
            )

        self.assertIsNotNone(result.errors)
        self.assertIn("Accès non autorisé", str(result.errors))

    def test_create_user_success_as_admin(self):
        with patch.multiple(
            auth_client,
            validate_token=Mock(return_value=Mock(user_id="admin-1", role="ADMIN")),
            create_user=Mock(return_value=make_user_response(username="agent2", role="AGENT")),
        ):
            result = schema.execute_sync(
                'mutation { createUser(username: "agent2", email: "agent2@example.com", password: "secret123", role: AGENT) '
                "{ username role } }",
                context_value=context(token="access-1"),
            )

        self.assertIsNone(result.errors)
        self.assertEqual(result.data["createUser"]["username"], "agent2")

    def test_deactivate_user_requires_admin_role(self):
        with patch.object(auth_client, "validate_token", return_value=Mock(user_id="user-1", role="COMPTABLE")):
            result = schema.execute_sync(
                'mutation { deactivateUser(id: "user-2") { username } }',
                context_value=context(token="access-1"),
            )

        self.assertIsNotNone(result.errors)

    def test_deactivate_user_success_as_admin(self):
        with patch.multiple(
            auth_client,
            validate_token=Mock(return_value=Mock(user_id="admin-1", role="ADMIN")),
            deactivate_user=Mock(return_value=make_user_response(username="agent2", is_active=False)),
        ):
            result = schema.execute_sync(
                'mutation { deactivateUser(id: "user-2") { username isActive } }',
                context_value=context(token="access-1"),
            )

        self.assertIsNone(result.errors)
        self.assertFalse(result.data["deactivateUser"]["isActive"])

    def test_create_user_with_invalid_token_raises(self):
        with patch.object(auth_client, "validate_token", side_effect=FakeRpcError("Token invalide ou expiré")):
            result = schema.execute_sync(
                'mutation { createUser(username: "x", email: "x@example.com", password: "secret123", role: AGENT) '
                "{ username } }",
                context_value=context(token="invalide"),
            )

        self.assertIsNotNone(result.errors)
        self.assertIn("Token invalide ou expiré", str(result.errors))
