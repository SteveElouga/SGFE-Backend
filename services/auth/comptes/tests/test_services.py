from datetime import timedelta
from unittest.mock import patch

from django.test import TestCase
from django.utils import timezone
from rest_framework_simplejwt.tokens import RefreshToken as RefreshTokenJWT

from comptes.email_client import EmailDeliveryError
from comptes.models import PasswordSetupToken, Role, User
from comptes.services import AuthenticationError, AuthService, PasswordSetupService, UserAdminService


class AuthServiceTests(TestCase):
    def setUp(self):
        self.user_admin = UserAdminService()
        self.auth = AuthService()
        User.objects.create_user(
            username="comptable1", email="comptable1@example.com", password="secret123", role=Role.COMPTABLE
        )

    def test_login_success_returns_tokens(self):
        access, refresh, expires_in = self.auth.login("comptable1", "secret123")
        self.assertTrue(access)
        self.assertTrue(refresh)
        self.assertGreater(expires_in, 0)

    def test_login_unknown_username_raises(self):
        with self.assertRaises(AuthenticationError):
            self.auth.login("inconnu", "secret123")

    def test_login_wrong_password_raises(self):
        with self.assertRaises(AuthenticationError):
            self.auth.login("comptable1", "wrongpassword")

    def test_login_with_unusable_password_raises(self):
        # Cas d'un compte créé par un admin, pas encore activé.
        User.objects.create_user(username="pending1", email="pending1@example.com", role=Role.AGENT)
        with self.assertRaises(AuthenticationError):
            self.auth.login("pending1", "n'importe quoi")

    def test_login_inactive_account_raises(self):
        user = self.user_admin.users.get_by_username("comptable1")
        user.is_active = False
        self.user_admin.users.save(user)
        with self.assertRaises(AuthenticationError):
            self.auth.login("comptable1", "secret123")

    def test_login_locks_after_max_attempts(self):
        for _ in range(5):
            with self.assertRaises(AuthenticationError):
                self.auth.login("comptable1", "wrongpassword")

        with self.assertRaises(AuthenticationError):
            self.auth.login("comptable1", "secret123")

    def test_validate_token_returns_user(self):
        access, _, _ = self.auth.login("comptable1", "secret123")
        user = self.auth.validate_token(access)
        self.assertEqual(user.username, "comptable1")

    def test_validate_token_invalid_raises(self):
        with self.assertRaises(AuthenticationError):
            self.auth.validate_token("token-invalide")

    def test_validate_token_deleted_user_raises(self):
        access, _, _ = self.auth.login("comptable1", "secret123")
        self.auth.users.get_by_username("comptable1").delete()
        with self.assertRaises(AuthenticationError):
            self.auth.validate_token(access)

    def test_validate_revoked_token_raises(self):
        access, _, _ = self.auth.login("comptable1", "secret123")
        self.auth.logout(access)
        with self.assertRaises(AuthenticationError):
            self.auth.validate_token(access)

    def test_refresh_token_returns_new_tokens(self):
        _, refresh, _ = self.auth.login("comptable1", "secret123")
        access, new_refresh, expires_in = self.auth.refresh_token(refresh)
        self.assertTrue(access)
        self.assertTrue(new_refresh)
        self.assertGreater(expires_in, 0)

    def test_refresh_token_invalid_raises(self):
        with self.assertRaises(AuthenticationError):
            self.auth.refresh_token("token-invalide")

    def test_refresh_token_deleted_user_raises(self):
        _, refresh, _ = self.auth.login("comptable1", "secret123")
        self.auth.users.get_by_username("comptable1").delete()
        with self.assertRaises(AuthenticationError):
            self.auth.refresh_token(refresh)

    def test_refresh_revoked_token_raises(self):
        _, refresh, _ = self.auth.login("comptable1", "secret123")
        refresh_obj = RefreshTokenJWT(refresh)
        self.auth.revoked_tokens.revoke(token_jti=refresh_obj["jti"], expires_at=timezone.now())
        with self.assertRaises(AuthenticationError):
            self.auth.refresh_token(refresh)

    def test_logout_revokes_token(self):
        access, _, _ = self.auth.login("comptable1", "secret123")
        self.auth.logout(access)
        with self.assertRaises(AuthenticationError):
            self.auth.validate_token(access)

    def test_logout_invalid_token_raises(self):
        with self.assertRaises(AuthenticationError):
            self.auth.logout("token-invalide")


class UserAdminServiceTests(TestCase):
    def setUp(self):
        self.user_admin = UserAdminService()
        self.send_patcher = patch("comptes.services.email_client.send")
        self.mock_send = self.send_patcher.start()
        self.addCleanup(self.send_patcher.stop)

    def test_create_user_has_no_usable_password(self):
        user = self.user_admin.create_user(username="agent3", email="agent3@example.com", role=Role.AGENT)
        self.assertEqual(user.role, Role.AGENT)
        self.assertFalse(user.has_usable_password())

    def test_create_user_sends_activation_email(self):
        self.user_admin.create_user(username="agent3b", email="agent3b@example.com", role=Role.AGENT)
        self.mock_send.assert_called_once()
        self.assertEqual(self.mock_send.call_args.kwargs["to_email"], "agent3b@example.com")

    def test_create_user_creates_password_setup_token(self):
        user = self.user_admin.create_user(username="agent3c", email="agent3c@example.com", role=Role.AGENT)
        self.assertTrue(PasswordSetupToken.objects.filter(user=user).exists())

    def test_get_user(self):
        created = self.user_admin.create_user(username="agent4", email="agent4@example.com", role=Role.AGENT)
        fetched = self.user_admin.get_user(str(created.id))
        self.assertEqual(fetched.username, "agent4")

    def test_update_user(self):
        created = self.user_admin.create_user(username="agent5", email="agent5@example.com", role=Role.AGENT)
        updated = self.user_admin.update_user(str(created.id), email="new@example.com", role=Role.ADMIN)
        self.assertEqual(updated.email, "new@example.com")
        self.assertEqual(updated.role, Role.ADMIN)

    def test_update_user_without_changes_keeps_values(self):
        created = self.user_admin.create_user(username="agent6", email="agent6@example.com", role=Role.AGENT)
        updated = self.user_admin.update_user(str(created.id), email="", role="")
        self.assertEqual(updated.email, "agent6@example.com")
        self.assertEqual(updated.role, Role.AGENT)

    def test_deactivate_user(self):
        created = self.user_admin.create_user(username="agent7", email="agent7@example.com", role=Role.AGENT)
        deactivated = self.user_admin.deactivate_user(str(created.id))
        self.assertFalse(deactivated.is_active)

    def test_list_users(self):
        self.user_admin.create_user(username="agent8", email="agent8@example.com", role=Role.AGENT)
        self.user_admin.create_user(username="agent9", email="agent9@example.com", role=Role.AGENT)
        usernames = {u.username for u in self.user_admin.list_users()}
        self.assertIn("agent8", usernames)
        self.assertIn("agent9", usernames)


class PasswordSetupServiceTests(TestCase):
    def setUp(self):
        self.service = PasswordSetupService()
        self.user = User.objects.create_user(
            username="comptable2", email="comptable2@example.com", password="oldpassword", role=Role.COMPTABLE
        )
        self.send_patcher = patch("comptes.services.email_client.send")
        self.mock_send = self.send_patcher.start()
        self.addCleanup(self.send_patcher.stop)

    def test_send_activation_email_creates_valid_token(self):
        self.service.send_activation_email(self.user)
        token = PasswordSetupToken.objects.get(user=self.user)
        self.assertTrue(token.is_valid())
        self.mock_send.assert_called_once()

    def test_request_password_reset_existing_email_sends_mail(self):
        self.service.request_password_reset("comptable2@example.com")
        self.assertTrue(PasswordSetupToken.objects.filter(user=self.user).exists())
        self.mock_send.assert_called_once()

    def test_request_password_reset_unknown_email_is_silent(self):
        self.service.request_password_reset("inconnu@example.com")
        self.mock_send.assert_not_called()
        self.assertFalse(PasswordSetupToken.objects.exists())

    def test_set_password_with_token_success(self):
        self.service.send_activation_email(self.user)
        token = PasswordSetupToken.objects.get(user=self.user)

        self.service.set_password_with_token(token.token, "newpassword123")

        self.user.refresh_from_db()
        self.assertTrue(self.user.check_password("newpassword123"))
        token.refresh_from_db()
        self.assertIsNotNone(token.used_at)

    def test_set_password_with_invalid_token_raises(self):
        with self.assertRaises(AuthenticationError):
            self.service.set_password_with_token("token-inexistant", "newpassword123")

    def test_set_password_with_expired_token_raises(self):
        token = PasswordSetupToken.objects.create(user=self.user, expires_at=timezone.now() - timedelta(hours=1))
        with self.assertRaises(AuthenticationError):
            self.service.set_password_with_token(token.token, "newpassword123")

    def test_set_password_with_already_used_token_raises(self):
        token = PasswordSetupToken.objects.create(user=self.user, expires_at=timezone.now() + timedelta(hours=1))
        token.mark_used()
        with self.assertRaises(AuthenticationError):
            self.service.set_password_with_token(token.token, "newpassword123")

    def test_email_delivery_error_propagates(self):
        self.mock_send.side_effect = EmailDeliveryError("Brevo a renvoyé 500")
        with self.assertRaises(EmailDeliveryError):
            self.service.send_activation_email(self.user)
