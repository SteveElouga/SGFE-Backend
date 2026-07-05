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
    def __init__(self, headers: dict | None = None, cookies: dict | None = None) -> None:
        self.headers = headers or {}
        self.COOKIES = cookies or {}


class FakeResponse:
    """Double minimal de HttpResponse : trace les cookies posés/supprimés."""

    def __init__(self) -> None:
        self.cookies_set: dict[str, str] = {}
        self.cookies_deleted: list[str] = []

    def set_cookie(self, key, value, **kwargs):
        self.cookies_set[key] = value

    def delete_cookie(self, key, **kwargs):
        self.cookies_deleted.append(key)


def context(token: str | None = None, refresh_cookie: str | None = None) -> dict:
    headers = {"Authorization": f"Bearer {token}"} if token else {}
    cookies = {"refresh_token": refresh_cookie} if refresh_cookie else {}
    return {"request": FakeRequest(headers=headers, cookies=cookies), "response": FakeResponse()}


def make_user_response(user_id="user-1", username="comptable1", role="COMPTABLE", is_active=True):
    return Mock(
        user_id=user_id,
        username=username,
        email=f"{username}@example.com",
        phone_number="+237690000001",
        role=role,
        is_active=is_active,
        created_at="2024-01-01T00:00:00",
    )


class LoginMutationTests(SimpleTestCase):
    def test_login_success_returns_auth_payload(self):
        ctx = context()
        with patch.multiple(
            auth_client,
            login=Mock(return_value=Mock(access_token="access-1", refresh_token="refresh-1", expires_in=86400)),
            validate_token=Mock(return_value=Mock(user_id="user-1", role="COMPTABLE")),
            get_user=Mock(return_value=make_user_response()),
        ):
            result = schema.execute_sync(
                'mutation { login(identifier: "comptable1", password: "secret123") '
                "{ accessToken expiresIn user { username role } } }",
                context_value=ctx,
            )

        self.assertIsNone(result.errors)
        self.assertEqual(result.data["login"]["accessToken"], "access-1")
        self.assertEqual(result.data["login"]["user"]["username"], "comptable1")
        self.assertEqual(result.data["login"]["user"]["role"], "COMPTABLE")

    def test_login_sets_httponly_refresh_cookie(self):
        ctx = context()
        with patch.multiple(
            auth_client,
            login=Mock(return_value=Mock(access_token="access-1", refresh_token="refresh-1", expires_in=86400)),
            validate_token=Mock(return_value=Mock(user_id="user-1", role="COMPTABLE")),
            get_user=Mock(return_value=make_user_response()),
        ):
            schema.execute_sync(
                'mutation { login(identifier: "comptable1", password: "secret123") { accessToken } }',
                context_value=ctx,
            )

        self.assertEqual(ctx["response"].cookies_set.get("refresh_token"), "refresh-1")

    def test_login_response_does_not_expose_refresh_token(self):
        # AuthPayload n'a plus de champ refreshToken : il n'existe que dans le cookie.
        # (le nom de la mutation `refreshToken` reste légitimement dans le SDL)
        self.assertNotIn("refreshToken: String", schema.as_str())

    def test_login_failure_returns_graphql_error_with_grpc_details(self):
        with patch.object(auth_client, "login", side_effect=FakeRpcError("Identifiants invalides")):
            result = schema.execute_sync(
                'mutation { login(identifier: "comptable1", password: "wrong") { accessToken } }',
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
                'mutation { login(identifier: "comptable1", password: "wrong") { accessToken } }',
                context_value=context(),
            )

        self.assertIsNotNone(result.errors)
        self.assertIn("Authentification requise ou invalide", str(result.errors))


class RefreshTokenMutationTests(SimpleTestCase):
    def test_refresh_token_success_reads_cookie_and_rotates_it(self):
        ctx = context(refresh_cookie="refresh-1")
        with patch.multiple(
            auth_client,
            refresh_token=Mock(return_value=Mock(access_token="access-2", refresh_token="refresh-2", expires_in=86400)),
            validate_token=Mock(return_value=Mock(user_id="user-1", role="COMPTABLE")),
            get_user=Mock(return_value=make_user_response()),
        ):
            result = schema.execute_sync("mutation { refreshToken { accessToken } }", context_value=ctx)
            auth_client.refresh_token.assert_called_once_with("refresh-1")

        self.assertIsNone(result.errors)
        self.assertEqual(result.data["refreshToken"]["accessToken"], "access-2")
        self.assertEqual(ctx["response"].cookies_set.get("refresh_token"), "refresh-2")

    def test_refresh_token_without_cookie_raises(self):
        result = schema.execute_sync("mutation { refreshToken { accessToken } }", context_value=context())

        self.assertIsNotNone(result.errors)
        self.assertIn("Refresh token manquant", str(result.errors))

    def test_refresh_token_failure_returns_graphql_error(self):
        with patch.object(auth_client, "refresh_token", side_effect=FakeRpcError("token invalide")):
            result = schema.execute_sync(
                "mutation { refreshToken { accessToken } }",
                context_value=context(refresh_cookie="refresh-1"),
            )

        self.assertIsNotNone(result.errors)


class LogoutMutationTests(SimpleTestCase):
    def test_logout_success_clears_refresh_cookie(self):
        ctx = context(token="access-1", refresh_cookie="refresh-1")
        with patch.object(auth_client, "logout", return_value=Mock(success=True)):
            result = schema.execute_sync("mutation { logout }", context_value=ctx)

        self.assertIsNone(result.errors)
        self.assertTrue(result.data["logout"])
        self.assertIn("refresh_token", ctx["response"].cookies_deleted)

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
                'mutation { createUser(username: "x", phoneNumber: "+237690000001", email: "x@example.com", role: AGENT) { username } }',
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
                'mutation { createUser(username: "agent2", phoneNumber: "+237690000002", '
                'email: "agent2@example.com", role: AGENT) { username role } }',
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
            # L'identité de l'appelant (issue de son JWT) est propagée pour le
            # contrôle d'auto-désactivation côté auth-service.
            auth_client.deactivate_user.assert_called_once_with("user-2", caller_id="admin-1")

        self.assertIsNone(result.errors)
        self.assertFalse(result.data["deactivateUser"]["isActive"])

    def test_deactivate_own_account_returns_error(self):
        with patch.multiple(
            auth_client,
            validate_token=Mock(return_value=Mock(user_id="admin-1", role="ADMIN")),
            deactivate_user=Mock(
                side_effect=FakeRpcError(
                    "Vous ne pouvez pas désactiver votre propre compte",
                    grpc.StatusCode.INVALID_ARGUMENT,
                )
            ),
        ):
            result = schema.execute_sync(
                'mutation { deactivateUser(id: "admin-1") { username } }',
                context_value=context(token="access-1"),
            )

        self.assertIsNotNone(result.errors)
        self.assertIn("votre propre compte", str(result.errors))

    def test_reactivate_user_requires_admin_role(self):
        with patch.object(auth_client, "validate_token", return_value=Mock(user_id="user-1", role="COMPTABLE")):
            result = schema.execute_sync(
                'mutation { reactivateUser(id: "user-2") { username } }',
                context_value=context(token="access-1"),
            )

        self.assertIsNotNone(result.errors)
        self.assertIn("Accès non autorisé", str(result.errors))

    def test_reactivate_user_success_as_admin(self):
        with patch.multiple(
            auth_client,
            validate_token=Mock(return_value=Mock(user_id="admin-1", role="ADMIN")),
            reactivate_user=Mock(return_value=make_user_response(username="agent2", is_active=True)),
        ):
            result = schema.execute_sync(
                'mutation { reactivateUser(id: "user-2") { username isActive } }',
                context_value=context(token="access-1"),
            )

        self.assertIsNone(result.errors)
        self.assertTrue(result.data["reactivateUser"]["isActive"])

    def test_reset_user_password_requires_admin_role(self):
        with patch.object(auth_client, "validate_token", return_value=Mock(user_id="user-1", role="COMPTABLE")):
            result = schema.execute_sync(
                'mutation { resetUserPassword(id: "user-2") { username } }',
                context_value=context(token="access-1"),
            )

        self.assertIsNotNone(result.errors)
        self.assertIn("Accès non autorisé", str(result.errors))

    def test_reset_user_password_success_as_admin(self):
        with patch.multiple(
            auth_client,
            validate_token=Mock(return_value=Mock(user_id="admin-1", role="ADMIN")),
            reset_user_password=Mock(return_value=make_user_response(username="agent2")),
        ):
            result = schema.execute_sync(
                'mutation { resetUserPassword(id: "user-2") { username } }',
                context_value=context(token="access-1"),
            )
            auth_client.reset_user_password.assert_called_once_with("user-2")

        self.assertIsNone(result.errors)
        self.assertEqual(result.data["resetUserPassword"]["username"], "agent2")

    def test_create_user_with_invalid_token_raises(self):
        with patch.object(auth_client, "validate_token", side_effect=FakeRpcError("Token invalide ou expiré")):
            result = schema.execute_sync(
                'mutation { createUser(username: "x", phoneNumber: "+237690000001", email: "x@example.com", role: AGENT) { username } }',
                context_value=context(token="invalide"),
            )

        self.assertIsNotNone(result.errors)
        self.assertIn("Token invalide ou expiré", str(result.errors))


class UsersQueryTests(SimpleTestCase):
    def test_users_requires_admin_role(self):
        with patch.object(auth_client, "validate_token", return_value=Mock(user_id="user-1", role="COMPTABLE")):
            result = schema.execute_sync("query { users { username } }", context_value=context(token="access-1"))

        self.assertIsNotNone(result.errors)
        self.assertIn("Accès non autorisé", str(result.errors))

    def test_users_returns_list_as_admin(self):
        with patch.multiple(
            auth_client,
            validate_token=Mock(return_value=Mock(user_id="admin-1", role="ADMIN")),
            list_users=Mock(
                return_value=Mock(
                    users=[
                        make_user_response(user_id="u-1", username="agent1", role="AGENT"),
                        make_user_response(user_id="u-2", username="comptable1", role="COMPTABLE"),
                    ]
                )
            ),
        ):
            result = schema.execute_sync("query { users { username role } }", context_value=context(token="access-1"))

        self.assertIsNone(result.errors)
        self.assertEqual(len(result.data["users"]), 2)
        usernames = {u["username"] for u in result.data["users"]}
        self.assertIn("agent1", usernames)
        self.assertIn("comptable1", usernames)

    def test_users_requires_auth(self):
        result = schema.execute_sync("query { users { username } }", context_value=context())

        self.assertIsNotNone(result.errors)
        self.assertIn("Authentification requise", str(result.errors))


class AgentsDisponiblesQueryTests(SimpleTestCase):
    def _list_users_mock(self):
        return Mock(
            return_value=Mock(
                users=[
                    make_user_response(user_id="u-1", username="agent_actif", role="AGENT", is_active=True),
                    make_user_response(user_id="u-2", username="agent_inactif", role="AGENT", is_active=False),
                    make_user_response(user_id="u-3", username="comptable1", role="COMPTABLE", is_active=True),
                    make_user_response(user_id="u-4", username="superviseur1", role="SUPERVISEUR", is_active=True),
                ]
            )
        )

    def test_ne_retourne_que_les_agents_actifs(self):
        with patch.multiple(
            auth_client,
            validate_token=Mock(return_value=Mock(user_id="admin-1", role="ADMIN")),
            list_users=self._list_users_mock(),
        ):
            result = schema.execute_sync(
                "query { agentsDisponibles { username role } }", context_value=context(token="access-1")
            )

        self.assertIsNone(result.errors)
        usernames = [u["username"] for u in result.data["agentsDisponibles"]]
        self.assertEqual(usernames, ["agent_actif"])

    def test_accessible_au_superviseur(self):
        with patch.multiple(
            auth_client,
            validate_token=Mock(return_value=Mock(user_id="sup-1", role="SUPERVISEUR")),
            list_users=self._list_users_mock(),
        ):
            result = schema.execute_sync(
                "query { agentsDisponibles { username } }", context_value=context(token="access-1")
            )

        self.assertIsNone(result.errors)
        self.assertEqual(len(result.data["agentsDisponibles"]), 1)

    def test_refuse_au_comptable(self):
        with patch.object(auth_client, "validate_token", return_value=Mock(user_id="c-1", role="COMPTABLE")):
            result = schema.execute_sync(
                "query { agentsDisponibles { username } }", context_value=context(token="access-1")
            )

        self.assertIsNotNone(result.errors)
        self.assertIn("Accès non autorisé", str(result.errors))

    def test_requires_auth(self):
        result = schema.execute_sync("query { agentsDisponibles { username } }", context_value=context())

        self.assertIsNotNone(result.errors)
        self.assertIn("Authentification requise", str(result.errors))


class PasswordSetupMutationTests(SimpleTestCase):
    def test_request_password_reset_returns_success(self):
        with patch.object(auth_client, "request_password_reset", return_value=Mock(success=True)):
            result = schema.execute_sync(
                'mutation { requestPasswordReset(email: "comptable1@example.com") }',
                context_value=context(),
            )

        self.assertIsNone(result.errors)
        self.assertTrue(result.data["requestPasswordReset"])

    def test_activate_account_success(self):
        with patch.object(auth_client, "set_password_with_token", return_value=Mock(success=True)) as mock_set:
            result = schema.execute_sync(
                'mutation { activateAccount(token: "abc123", password: "secret123") }',
                context_value=context(),
            )
            mock_set.assert_called_once_with("abc123", "secret123")

        self.assertIsNone(result.errors)
        self.assertTrue(result.data["activateAccount"])

    def test_activate_account_invalid_token_raises(self):
        with patch.object(auth_client, "set_password_with_token", side_effect=FakeRpcError("Token invalide")):
            result = schema.execute_sync(
                'mutation { activateAccount(token: "invalide", password: "secret123") }',
                context_value=context(),
            )

        self.assertIsNotNone(result.errors)
        self.assertIn("Token invalide", str(result.errors))

    def test_reset_password_success(self):
        with patch.object(auth_client, "set_password_with_token", return_value=Mock(success=True)) as mock_set:
            result = schema.execute_sync(
                'mutation { resetPassword(token: "abc123", password: "secret123") }',
                context_value=context(),
            )
            mock_set.assert_called_once_with("abc123", "secret123")

        self.assertIsNone(result.errors)
        self.assertTrue(result.data["resetPassword"])
