from django.test import TestCase
from django.utils import timezone
from rest_framework_simplejwt.tokens import RefreshToken as RefreshTokenJWT

from comptes.models import Role
from comptes.services import AuthenticationError, AuthService, UserAdminService


class AuthServiceTests(TestCase):
    def setUp(self):
        self.user_admin = UserAdminService()
        self.auth = AuthService()
        self.user_admin.create_user(
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

    def test_create_user(self):
        user = self.user_admin.create_user(
            username="agent3", email="agent3@example.com", password="secret123", role=Role.AGENT
        )
        self.assertEqual(user.role, Role.AGENT)
        self.assertTrue(user.check_password("secret123"))

    def test_get_user(self):
        created = self.user_admin.create_user(
            username="agent4", email="agent4@example.com", password="secret123", role=Role.AGENT
        )
        fetched = self.user_admin.get_user(str(created.id))
        self.assertEqual(fetched.username, "agent4")

    def test_update_user(self):
        created = self.user_admin.create_user(
            username="agent5", email="agent5@example.com", password="secret123", role=Role.AGENT
        )
        updated = self.user_admin.update_user(str(created.id), email="new@example.com", role=Role.ADMIN)
        self.assertEqual(updated.email, "new@example.com")
        self.assertEqual(updated.role, Role.ADMIN)

    def test_update_user_without_changes_keeps_values(self):
        created = self.user_admin.create_user(
            username="agent6", email="agent6@example.com", password="secret123", role=Role.AGENT
        )
        updated = self.user_admin.update_user(str(created.id), email="", role="")
        self.assertEqual(updated.email, "agent6@example.com")
        self.assertEqual(updated.role, Role.AGENT)

    def test_deactivate_user(self):
        created = self.user_admin.create_user(
            username="agent7", email="agent7@example.com", password="secret123", role=Role.AGENT
        )
        deactivated = self.user_admin.deactivate_user(str(created.id))
        self.assertFalse(deactivated.is_active)

    def test_list_users(self):
        self.user_admin.create_user(
            username="agent8", email="agent8@example.com", password="secret123", role=Role.AGENT
        )
        self.user_admin.create_user(
            username="agent9", email="agent9@example.com", password="secret123", role=Role.AGENT
        )
        usernames = {u.username for u in self.user_admin.list_users()}
        self.assertIn("agent8", usernames)
        self.assertIn("agent9", usernames)
