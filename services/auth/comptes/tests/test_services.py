from datetime import timedelta
from unittest.mock import patch

from django.test import TestCase
from django.utils import timezone
from rest_framework_simplejwt.tokens import RefreshToken as RefreshTokenJWT

from comptes.email_client import EmailDeliveryError
from comptes.models import PasswordSetupToken, PhoneOtpToken, Role, User
from comptes.services import AuthenticationError, AuthService, PasswordSetupService, PhoneOtpService, UserAdminService


class AuthServiceTests(TestCase):
    def setUp(self):
        self.user_admin = UserAdminService()
        self.auth = AuthService()
        User.objects.create_user(
            username="comptable1",
            email="comptable1@example.com",
            password="secret123",
            role=Role.COMPTABLE,
            phone_number="+237690000001",
        )

    def test_login_success_returns_tokens(self):
        access, refresh, expires_in = self.auth.login("comptable1", "secret123")
        self.assertTrue(access)
        self.assertTrue(refresh)
        self.assertGreater(expires_in, 0)

    def test_login_by_phone_returns_tokens(self):
        access, refresh, _ = self.auth.login("+237690000001", "secret123")
        self.assertTrue(access)
        self.assertTrue(refresh)

    def test_login_unknown_username_raises(self):
        with self.assertRaises(AuthenticationError):
            self.auth.login("inconnu", "secret123")

    def test_login_wrong_password_raises(self):
        with self.assertRaises(AuthenticationError):
            self.auth.login("comptable1", "wrongpassword")

    def test_login_with_unusable_password_raises(self):
        User.objects.create_user(
            username="pending1",
            email="pending1@example.com",
            role=Role.AGENT,
            phone_number="+237690000002",
        )
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

    def test_refresh_token_revoque_lancien_refresh_token(self):
        """Régression ANO-006 : après un refresh, l'ancien refresh token ne
        doit plus pouvoir être réutilisé (rotation)."""
        _, refresh, _ = self.auth.login("comptable1", "secret123")
        self.auth.refresh_token(refresh)
        with self.assertRaises(AuthenticationError):
            self.auth.refresh_token(refresh)

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
        # E-mail mocké pour les créations ADMIN (activation par e-mail).
        self.send_patcher = patch("comptes.services.email_client.send")
        self.mock_send = self.send_patcher.start()
        self.addCleanup(self.send_patcher.stop)
        # WhatsApp mocké pour les créations non-ADMIN (activation par OTP).
        self.whatsapp_patcher = patch("comptes.services.whatsapp_client.send")
        self.mock_whatsapp = self.whatsapp_patcher.start()
        self.addCleanup(self.whatsapp_patcher.stop)

    def test_create_agent_has_no_usable_password(self):
        user = self.user_admin.create_user(username="agent3", phone_number="+237690000011", role=Role.AGENT)
        self.assertEqual(user.role, Role.AGENT)
        self.assertFalse(user.has_usable_password())

    def test_create_agent_is_inactive_until_activation(self):
        user = self.user_admin.create_user(username="agent3z", phone_number="+237690000019", role=Role.AGENT)
        self.assertFalse(user.is_active)

    def test_create_admin_is_inactive_until_activation(self):
        user = self.user_admin.create_user(
            username="admin3z",
            email="admin3z@example.com",
            phone_number="+237690000018",
            role=Role.ADMIN,
        )
        self.assertFalse(user.is_active)

    def test_create_agent_sends_whatsapp_otp(self):
        self.user_admin.create_user(username="agent3b", phone_number="+237690000012", role=Role.AGENT)
        self.mock_whatsapp.assert_called_once()
        self.mock_send.assert_not_called()

    def test_create_agent_creates_phone_otp_token(self):
        user = self.user_admin.create_user(username="agent3c", phone_number="+237690000013", role=Role.AGENT)
        self.assertTrue(PhoneOtpToken.objects.filter(user=user).exists())

    def test_create_admin_sends_activation_email(self):
        self.user_admin.create_user(
            username="admin2",
            email="admin2@example.com",
            phone_number="+237690000014",
            role=Role.ADMIN,
        )
        self.mock_send.assert_called_once()
        call_kwargs = self.mock_send.call_args.kwargs
        self.assertEqual(call_kwargs["to_email"], "admin2@example.com")

    def test_create_admin_creates_password_setup_token(self):
        user = self.user_admin.create_user(
            username="admin3",
            email="admin3@example.com",
            phone_number="+237690000015",
            role=Role.ADMIN,
        )
        self.assertTrue(PasswordSetupToken.objects.filter(user=user).exists())

    def test_create_admin_without_email_raises(self):
        with self.assertRaises(ValueError):
            self.user_admin.create_user(username="admin4", phone_number="+237690000016", role=Role.ADMIN)

    def test_get_user(self):
        created = self.user_admin.create_user(username="agent4", phone_number="+237690000021", role=Role.AGENT)
        fetched = self.user_admin.get_user(str(created.id))
        self.assertEqual(fetched.username, "agent4")

    def test_update_user(self):
        created = self.user_admin.create_user(username="agent5", phone_number="+237690000022", role=Role.AGENT)
        updated = self.user_admin.update_user(str(created.id), email="new@example.com", role=Role.ADMIN)
        self.assertEqual(updated.email, "new@example.com")
        self.assertEqual(updated.role, Role.ADMIN)

    def test_update_user_without_changes_keeps_values(self):
        created = self.user_admin.create_user(username="agent6", phone_number="+237690000023", role=Role.AGENT)
        updated = self.user_admin.update_user(str(created.id), email="", role="")
        self.assertEqual(updated.role, Role.AGENT)

    def test_update_user_phone_change_resend_otp_si_non_active(self):
        """Quand le téléphone change pour un compte en attente d'activation, l'OTP est renvoyé."""
        created = self.user_admin.create_user(username="agent7b", phone_number="+237690000030", role=Role.AGENT)
        # Après create_user, un premier OTP a déjà été envoyé
        first_call_count = self.mock_whatsapp.call_count

        self.user_admin.update_user(str(created.id), email="", role="", phone_number="+237690000031")

        self.assertEqual(self.mock_whatsapp.call_count, first_call_count + 1)
        last_call_kwargs = self.mock_whatsapp.call_args.kwargs
        self.assertEqual(last_call_kwargs["to_phone"], "+237690000031")

    def test_update_user_phone_change_ne_renvoie_pas_otp_si_actif(self):
        """Aucun OTP n'est renvoyé si l'utilisateur est déjà activé."""
        created = self.user_admin.create_user(username="agent7c", phone_number="+237690000032", role=Role.AGENT)
        created.is_active = True
        created.set_password("S3cr3t!")
        created.save()
        before = self.mock_whatsapp.call_count

        self.user_admin.update_user(str(created.id), email="", role="", phone_number="+237690000033")

        self.assertEqual(self.mock_whatsapp.call_count, before)

    def test_update_user_admin_phone_change_ne_renvoie_pas_otp(self):
        """Un ADMIN s'active par e-mail, pas par OTP — le changement de téléphone n'envoie rien."""
        created = self.user_admin.create_user(
            username="admin_phone",
            phone_number="+237690000034",
            role=Role.ADMIN,
            email="adminp@example.com",
        )
        before = self.mock_whatsapp.call_count

        self.user_admin.update_user(str(created.id), email="", role="", phone_number="+237690000035")

        self.assertEqual(self.mock_whatsapp.call_count, before)

    def test_update_user_admin_email_change_resend_activation_si_en_attente(self):
        """Quand l'e-mail d'un ADMIN en attente d'activation change, un nouveau lien
        d'activation est envoyé sur le nouvel e-mail (symétrique du renvoi d'OTP)."""
        created = self.user_admin.create_user(
            username="admin_pending_email",
            email="wrong@example.com",
            phone_number="+237690000060",
            role=Role.ADMIN,
        )
        # create_user a déjà envoyé un premier lien d'activation.
        before = self.mock_send.call_count

        self.user_admin.update_user(str(created.id), email="right@example.com", role="")

        self.assertEqual(self.mock_send.call_count, before + 1)
        self.assertEqual(self.mock_send.call_args.kwargs["to_email"], "right@example.com")

    def test_update_user_admin_email_change_ne_renvoie_rien_si_actif(self):
        """Un ADMIN déjà activé n'est jamais impacté par un changement d'e-mail."""
        created = self.user_admin.create_user(
            username="admin_active_email",
            email="admin_ae@example.com",
            phone_number="+237690000061",
            role=Role.ADMIN,
        )
        created.set_password("S3cr3t!")
        created.is_active = True
        created.save()
        before = self.mock_send.call_count

        self.user_admin.update_user(str(created.id), email="admin_ae2@example.com", role="")

        self.assertEqual(self.mock_send.call_count, before)

    def test_deactivate_user(self):
        created = self.user_admin.create_user(username="agent7", phone_number="+237690000024", role=Role.AGENT)
        deactivated = self.user_admin.deactivate_user(str(created.id))
        self.assertFalse(deactivated.is_active)

    def test_reactivate_user(self):
        created = self.user_admin.create_user(username="agent7d", phone_number="+237690000027", role=Role.AGENT)
        self.user_admin.deactivate_user(str(created.id))
        reactivated = self.user_admin.reactivate_user(str(created.id))
        self.assertTrue(reactivated.is_active)

    def test_deactivate_own_account_raises(self):
        admin = User.objects.create_user(
            username="admin_self",
            email="admin_self@example.com",
            password="S3cr3t!",
            role=Role.ADMIN,
            phone_number="+237690000050",
        )
        # Un second admin actif pour isoler le contrôle d'auto-désactivation.
        User.objects.create_user(
            username="admin_other",
            email="admin_other@example.com",
            password="S3cr3t!",
            role=Role.ADMIN,
            phone_number="+237690000051",
        )
        with self.assertRaisesMessage(ValueError, "votre propre compte"):
            self.user_admin.deactivate_user(str(admin.id), caller_id=str(admin.id))

    def test_deactivate_last_active_admin_raises(self):
        admin = User.objects.create_user(
            username="admin_last",
            email="admin_last@example.com",
            password="S3cr3t!",
            role=Role.ADMIN,
            phone_number="+237690000052",
        )
        with self.assertRaisesMessage(ValueError, "dernier administrateur actif"):
            self.user_admin.deactivate_user(str(admin.id))

    def test_deactivate_admin_ok_when_another_active_admin_exists(self):
        admin1 = User.objects.create_user(
            username="admin_a",
            email="admin_a@example.com",
            password="S3cr3t!",
            role=Role.ADMIN,
            phone_number="+237690000053",
        )
        User.objects.create_user(
            username="admin_b",
            email="admin_b@example.com",
            password="S3cr3t!",
            role=Role.ADMIN,
            phone_number="+237690000054",
        )
        result = self.user_admin.deactivate_user(str(admin1.id))
        self.assertFalse(result.is_active)

    def test_resend_credentials_non_admin_sends_otp(self):
        created = self.user_admin.create_user(username="agent7e", phone_number="+237690000028", role=Role.AGENT)
        before = self.mock_whatsapp.call_count
        self.user_admin.resend_credentials(str(created.id))
        self.assertEqual(self.mock_whatsapp.call_count, before + 1)
        self.mock_send.assert_not_called()

    def test_resend_credentials_admin_pending_sends_activation_email(self):
        created = self.user_admin.create_user(
            username="admin_pending",
            email="admin_pending@example.com",
            phone_number="+237690000029",
            role=Role.ADMIN,
        )
        # create_user a déjà envoyé un e-mail d'activation ; on repart de ce compteur.
        before = self.mock_send.call_count
        self.user_admin.resend_credentials(str(created.id))
        self.assertEqual(self.mock_send.call_count, before + 1)
        subject = self.mock_send.call_args.kwargs["subject"]
        self.assertIn("Activez", subject)

    def test_resend_credentials_admin_activated_sends_reset_email(self):
        created = self.user_admin.create_user(
            username="admin_activated",
            email="admin_activated@example.com",
            phone_number="+237690000039",
            role=Role.ADMIN,
        )
        created.set_password("S3cr3t!")
        created.is_active = True
        created.save()
        before = self.mock_send.call_count
        self.user_admin.resend_credentials(str(created.id))
        self.assertEqual(self.mock_send.call_count, before + 1)
        subject = self.mock_send.call_args.kwargs["subject"]
        self.assertIn("Réinitialisation", subject)

    def test_list_users(self):
        self.user_admin.create_user(username="agent8", phone_number="+237690000025", role=Role.AGENT)
        self.user_admin.create_user(username="agent9", phone_number="+237690000026", role=Role.AGENT)
        usernames = {u.username for u in self.user_admin.list_users()}
        self.assertIn("agent8", usernames)
        self.assertIn("agent9", usernames)


class PasswordSetupServiceTests(TestCase):
    def setUp(self):
        self.service = PasswordSetupService()
        # La réinitialisation de mot de passe par e-mail est réservée aux ADMIN.
        self.user = User.objects.create_user(
            username="admin_reset",
            email="admin_reset@example.com",
            password="oldpassword",
            role=Role.ADMIN,
            phone_number="+237690000030",
        )
        self.send_patcher = patch("comptes.services.email_client.send")
        self.mock_send = self.send_patcher.start()
        self.addCleanup(self.send_patcher.stop)

    def test_send_activation_email_creates_valid_token(self):
        self.service.send_activation_email(self.user)
        token = PasswordSetupToken.objects.get(user=self.user)
        self.assertTrue(token.is_valid())
        self.mock_send.assert_called_once()

    def test_request_password_reset_admin_sends_email(self):
        self.service.request_password_reset("admin_reset@example.com")
        self.assertTrue(PasswordSetupToken.objects.filter(user=self.user).exists())
        self.mock_send.assert_called_once()

    def test_request_password_reset_non_admin_is_silent(self):
        User.objects.create_user(
            username="comptable_reset",
            email="comptable_reset@example.com",
            password="pwd",
            role=Role.COMPTABLE,
            phone_number="+237690000031",
        )
        self.service.request_password_reset("comptable_reset@example.com")
        self.mock_send.assert_not_called()
        self.assertFalse(PasswordSetupToken.objects.filter(user__username="comptable_reset").exists())

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
        self.assertTrue(self.user.is_active)
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


class PhoneOtpServiceTests(TestCase):
    def setUp(self):
        self.service = PhoneOtpService()
        self.user = User.objects.create_user(
            username="agent_otp",
            phone_number="+237690000040",
            password="secret123",
            role=Role.AGENT,
        )
        self.whatsapp_patcher = patch("comptes.services.whatsapp_client.send")
        self.mock_whatsapp = self.whatsapp_patcher.start()
        self.addCleanup(self.whatsapp_patcher.stop)

    def test_send_otp_creates_token_and_sends_whatsapp(self):
        self.service.send_otp(self.user)
        self.assertTrue(PhoneOtpToken.objects.filter(user=self.user).exists())
        self.mock_whatsapp.assert_called_once()
        call_args = self.mock_whatsapp.call_args
        self.assertEqual(call_args.kwargs["to_phone"], "+237690000040")

    def test_send_otp_message_contient_lien_activation(self):
        self.service.send_otp(self.user)
        message = self.mock_whatsapp.call_args.kwargs["message"]
        self.assertIn("/activer-compte", message)
        self.assertIn("%2B237690000040", message)

    def test_request_otp_by_phone_sends_otp(self):
        self.service.request_otp_by_phone("+237690000040")
        self.mock_whatsapp.assert_called_once()

    def test_request_otp_unknown_phone_is_silent(self):
        self.service.request_otp_by_phone("+237600000000")
        self.mock_whatsapp.assert_not_called()

    def test_verify_otp_and_set_password_success(self):
        self.service.send_otp(self.user)
        import re

        raw_otp = re.search(r"\*(\d{6})\*", self.mock_whatsapp.call_args.kwargs["message"]).group(1)

        self.service.verify_otp_and_set_password("+237690000040", raw_otp, "newpassword123")
        self.user.refresh_from_db()
        self.assertTrue(self.user.check_password("newpassword123"))
        self.assertTrue(self.user.is_active)

    def test_verify_otp_actives_pending_user(self):
        """Un compte en attente d'activation (is_active=False, sans mot de passe) doit devenir actif."""
        pending = User.objects.create_user(
            username="pending_otp",
            phone_number="+237690000041",
            role=Role.AGENT,
            is_active=False,
        )
        self.assertFalse(pending.is_active)
        self.service.send_otp(pending)
        import re

        raw_otp = re.search(r"\*(\d{6})\*", self.mock_whatsapp.call_args.kwargs["message"]).group(1)

        self.service.verify_otp_and_set_password("+237690000041", raw_otp, "newpassword123")
        pending.refresh_from_db()
        self.assertTrue(pending.is_active)

    def test_request_otp_blocked_for_deactivated_user_with_password(self):
        """Un compte désactivé par un admin (is_active=False + mot de passe) ne doit pas recevoir d'OTP."""
        self.user.is_active = False
        self.user.save()
        self.service.request_otp_by_phone("+237690000040")
        self.mock_whatsapp.assert_not_called()

    def test_request_otp_allowed_for_pending_activation_user(self):
        """Un compte en attente d'activation (is_active=False + sans mot de passe) peut recevoir un OTP."""
        pending = User.objects.create_user(
            username="pending_resend",
            phone_number="+237690000042",
            role=Role.AGENT,
            is_active=False,
        )
        self.assertFalse(pending.is_active)
        self.service.request_otp_by_phone("+237690000042")
        self.mock_whatsapp.assert_called_once()

    def test_verify_wrong_otp_raises(self):
        self.service.send_otp(self.user)
        with self.assertRaises(AuthenticationError):
            self.service.verify_otp_and_set_password("+237690000040", "000000", "newpass")

    def test_previous_otps_invalidated_on_new_send(self):
        self.service.send_otp(self.user)
        self.service.send_otp(self.user)
        valid_tokens = PhoneOtpToken.objects.filter(user=self.user, used_at__isnull=True)
        self.assertEqual(valid_tokens.count(), 1)
