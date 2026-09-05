import sys
from datetime import timedelta
from pathlib import Path
from unittest.mock import MagicMock, patch

import grpc
from django.conf import settings
from django.test import TestCase
from django.utils import timezone

from comptes.grpc_server import AuthServiceServicer
from comptes.models import PasswordSetupToken, Role, User
from comptes.services import AuthenticationError

# Le fichier _grpc.py généré fait un `import auth_service_pb2` bare — il faut
# que le dossier proto/ soit dans sys.path avant l'import (voir grpc_server.py).
sys.path.insert(0, str(Path(settings.BASE_DIR) / "proto"))

import auth_service_pb2 as pb


def _mock_context() -> MagicMock:
    # En appel direct (hors serveur gRPC réel), ErrorHandlingInterceptor n'est
    # pas dans la boucle : les méthodes du servicer laissent donc simplement
    # remonter l'exception Python (voir test_grpc_interceptors.py pour la
    # conversion en code gRPC, elle, testée séparément) — abort() n'est donc
    # jamais appelé ici. Même motif que campagnes/tests/test_grpc.py.
    return MagicMock(spec=grpc.ServicerContext)


class AuthServiceServicerTests(TestCase):
    def setUp(self) -> None:
        self.servicer = AuthServiceServicer()
        self.context = _mock_context()
        self.user = User.objects.create_user(
            username="comptable_grpc",
            email="comptable_grpc@example.com",
            password="secret123",
            role=Role.COMPTABLE,
            phone_number="+237690000001",
        )
        self.send_patcher = patch("comptes.services.email_client.send")
        self.mock_send = self.send_patcher.start()
        self.addCleanup(self.send_patcher.stop)

    def test_login_success(self) -> None:
        response = self.servicer.Login(pb.LoginRequest(identifier="comptable_grpc", password="secret123"), self.context)
        self.assertTrue(response.access_token)
        self.assertTrue(response.refresh_token)

    def test_login_by_phone_success(self) -> None:
        response = self.servicer.Login(pb.LoginRequest(identifier="+237690000001", password="secret123"), self.context)
        self.assertTrue(response.access_token)

    def test_login_failure_raises_authentication_error(self) -> None:
        with self.assertRaises(AuthenticationError):
            self.servicer.Login(pb.LoginRequest(identifier="comptable_grpc", password="wrong"), self.context)

    def test_validate_token_success(self) -> None:
        login = self.servicer.Login(pb.LoginRequest(identifier="comptable_grpc", password="secret123"), self.context)
        payload = self.servicer.ValidateToken(pb.TokenRequest(token=login.access_token), self.context)
        self.assertEqual(payload.username, "comptable_grpc")

    def test_validate_token_failure_raises_authentication_error(self) -> None:
        with self.assertRaises(AuthenticationError):
            self.servicer.ValidateToken(pb.TokenRequest(token="invalide"), self.context)

    def test_refresh_token_success(self) -> None:
        login = self.servicer.Login(pb.LoginRequest(identifier="comptable_grpc", password="secret123"), self.context)
        refreshed = self.servicer.RefreshToken(pb.RefreshRequest(refresh_token=login.refresh_token), self.context)
        self.assertTrue(refreshed.access_token)

    def test_refresh_token_failure_raises_authentication_error(self) -> None:
        with self.assertRaises(AuthenticationError):
            self.servicer.RefreshToken(pb.RefreshRequest(refresh_token="invalide"), self.context)

    def test_logout_success(self) -> None:
        login = self.servicer.Login(pb.LoginRequest(identifier="comptable_grpc", password="secret123"), self.context)
        response = self.servicer.Logout(pb.TokenRequest(token=login.access_token), self.context)
        self.assertTrue(response.success)

    def test_logout_failure_raises_authentication_error(self) -> None:
        """Régression ANO-020 : Logout doit désormais lever AuthenticationError
        (géré par ErrorHandlingInterceptor -> UNAUTHENTICATED) comme les
        autres RPC de ce servicer, au lieu de retourner success=False."""
        with self.assertRaises(AuthenticationError):
            self.servicer.Logout(pb.TokenRequest(token="invalide"), self.context)

    def test_create_admin_sends_activation_email(self) -> None:
        response = self.servicer.CreateUser(
            pb.CreateUserRequest(
                username="admin_grpc2",
                email="admin_grpc2@example.com",
                phone_number="+237690000002",
                role=Role.ADMIN,
            ),
            self.context,
        )
        self.assertEqual(response.username, "admin_grpc2")
        self.assertTrue(response.user_id)
        self.mock_send.assert_called_once()

    def test_create_agent_sends_whatsapp_otp(self) -> None:
        with patch("comptes.services.whatsapp_client.send") as mock_wa:
            response = self.servicer.CreateUser(
                pb.CreateUserRequest(
                    username="agent_grpc",
                    phone_number="+237690000003",
                    role=Role.AGENT,
                ),
                self.context,
            )
        self.assertEqual(response.username, "agent_grpc")
        self.assertFalse(response.email)
        mock_wa.assert_called_once()

    def test_get_user(self) -> None:
        response = self.servicer.GetUser(pb.UserIdRequest(user_id=str(self.user.id)), self.context)
        self.assertEqual(response.username, "comptable_grpc")
        self.assertEqual(response.phone_number, "+237690000001")

    def test_update_user(self) -> None:
        response = self.servicer.UpdateUser(
            pb.UpdateUserRequest(user_id=str(self.user.id), email="updated@example.com", role=Role.ADMIN),
            self.context,
        )
        self.assertEqual(response.email, "updated@example.com")
        self.assertEqual(response.role, Role.ADMIN)

    def test_deactivate_user(self) -> None:
        response = self.servicer.DeactivateUser(pb.DeactivateUserRequest(user_id=str(self.user.id)), self.context)
        self.assertFalse(response.is_active)

    def test_deactivate_own_account_raises(self) -> None:
        admin = User.objects.create_user(
            username="admin_grpc_self",
            email="admin_grpc_self@example.com",
            password="secret123",
            role=Role.ADMIN,
            phone_number="+237690000096",
        )
        with self.assertRaises(ValueError):
            self.servicer.DeactivateUser(
                pb.DeactivateUserRequest(user_id=str(admin.id), caller_id=str(admin.id)), self.context
            )

    def test_reactivate_user(self) -> None:
        self.servicer.DeactivateUser(pb.DeactivateUserRequest(user_id=str(self.user.id)), self.context)
        response = self.servicer.ReactivateUser(pb.UserIdRequest(user_id=str(self.user.id)), self.context)
        self.assertTrue(response.is_active)

    def test_reset_user_password_admin_activated_sends_email(self) -> None:
        admin = User.objects.create_user(
            username="admin_grpc_reset",
            email="admin_grpc_reset@example.com",
            password="secret123",
            role=Role.ADMIN,
            phone_number="+237690000098",
        )
        response = self.servicer.ResetUserPassword(pb.UserIdRequest(user_id=str(admin.id)), self.context)
        self.assertEqual(response.username, "admin_grpc_reset")
        self.mock_send.assert_called_once()

    def test_reset_user_password_non_admin_sends_whatsapp(self) -> None:
        agent = User.objects.create_user(
            username="agent_grpc_reset",
            role=Role.AGENT,
            phone_number="+237690000097",
            is_active=False,
        )
        with patch("comptes.services.whatsapp_client.send") as mock_wa:
            response = self.servicer.ResetUserPassword(pb.UserIdRequest(user_id=str(agent.id)), self.context)
        self.assertEqual(response.username, "agent_grpc_reset")
        mock_wa.assert_called_once()

    def test_list_users(self) -> None:
        response = self.servicer.ListUsers(pb.EmptyRequest(), self.context)
        usernames = {u.username for u in response.users}
        self.assertIn("comptable_grpc", usernames)

    def test_request_password_reset(self) -> None:
        admin = User.objects.create_user(
            username="admin_reset",
            email="admin_reset@example.com",
            password="secret",
            role=Role.ADMIN,
            phone_number="+237690000099",
        )
        response = self.servicer.RequestPasswordReset(pb.EmailRequest(email=admin.email), self.context)
        self.assertTrue(response.success)
        self.mock_send.assert_called_once()

    def test_request_password_reset_unknown_email_still_succeeds(self) -> None:
        response = self.servicer.RequestPasswordReset(pb.EmailRequest(email="inconnu@example.com"), self.context)
        self.assertTrue(response.success)
        self.mock_send.assert_not_called()

    def test_set_password_with_token_success(self) -> None:
        token = PasswordSetupToken.objects.create(user=self.user, expires_at=timezone.now() + timedelta(hours=1))
        response = self.servicer.SetPasswordWithToken(
            pb.SetPasswordRequest(token=token.token, new_password="nouveaumotdepasse"), self.context
        )
        self.assertTrue(response.success)
        self.user.refresh_from_db()
        self.assertTrue(self.user.check_password("nouveaumotdepasse"))

    def test_set_password_with_invalid_token_raises(self) -> None:
        with self.assertRaises(AuthenticationError):
            self.servicer.SetPasswordWithToken(
                pb.SetPasswordRequest(token="invalide", new_password="nouveaumotdepasse"), self.context
            )


class DeactivateUserRevalidationRoleTests(TestCase):
    """Défense en profondeur (voir docs/CONFORMITE_SOC2_OWASP.md §3.1 A01,
    plan de remédiation item #3) : `DeactivateUser` revalide le rôle de
    l'appelant à partir de l'identité propagée par la gateway
    (`get_caller()`), en plus du RBAC déjà appliqué côté gateway
    (`gateway/schema/auth_mutations.py`, `require_role(info, "ADMIN")`).

    Compromis assumé (documenté sur `_revalider_role_deactivate`) : ce
    filet ne bloque JAMAIS l'appel, même avec un mauvais rôle ou une
    identité absente — il se contente de journaliser un avertissement.
    """

    def setUp(self) -> None:
        self.servicer = AuthServiceServicer()
        self.context = _mock_context()
        self.user = User.objects.create_user(
            username="a_desactiver",
            email="a_desactiver@example.com",
            password="secret123",
            role=Role.COMPTABLE,
            phone_number="+237690000097",
        )

    def _poser_identite(self, role: str) -> None:
        from comptes.grpc_interceptors import CallerIdentity, caller_identity

        jeton = caller_identity.set(CallerIdentity(user_id="u-1", username="testeur", role=role))
        self.addCleanup(caller_identity.reset, jeton)

    @patch("comptes.grpc_server.logger")
    def test_role_admin_passe_sans_avertissement_de_role(self, mock_logger: MagicMock) -> None:
        self._poser_identite("ADMIN")
        response = self.servicer.DeactivateUser(pb.DeactivateUserRequest(user_id=str(self.user.id)), self.context)
        self.assertFalse(response.is_active)
        for appel in mock_logger.warning.call_args_list:
            self.assertNotIn("hors de l'ensemble autorisé", appel.args[0])

    def test_role_non_autorise_journalise_un_avertissement_mais_passe(self) -> None:
        self._poser_identite("COMPTABLE")
        with self.assertLogs("comptes.grpc_server", level="WARNING") as journaux:
            response = self.servicer.DeactivateUser(pb.DeactivateUserRequest(user_id=str(self.user.id)), self.context)
        self.assertFalse(response.is_active)  # jamais bloqué (voir docstring de la classe)
        trace = "\n".join(journaux.output)
        self.assertIn("DeactivateUser", trace)
        self.assertIn("hors de l'ensemble autorisé", trace)
        self.assertIn("COMPTABLE", trace)

    def test_sans_identite_reste_retrocompatible(self) -> None:
        """Aucune identité propagée (appel hors gateway, ou service-à-service
        légitime) : comportement inchangé — aucune exception."""
        response = self.servicer.DeactivateUser(pb.DeactivateUserRequest(user_id=str(self.user.id)), self.context)
        self.assertFalse(response.is_active)
