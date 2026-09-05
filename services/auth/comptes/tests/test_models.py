from django.test import TestCase

from comptes.models import Role, User


class UserModelTests(TestCase):
    def test_create_user_hashes_password(self) -> None:
        user = User.objects.create_user(
            username="agent1", email="agent1@example.com", password="secret123", role=Role.AGENT
        )
        self.assertNotEqual(user.password, "secret123")
        self.assertTrue(user.check_password("secret123"))
        self.assertEqual(user.role, Role.AGENT)
        self.assertTrue(user.is_active)

    def test_create_user_without_username_raises(self) -> None:
        with self.assertRaises(ValueError):
            User.objects.create_user(username="", email="x@example.com", password="secret123", role=Role.AGENT)

    def test_create_superuser_defaults_to_admin(self) -> None:
        admin = User.objects.create_superuser(username="admin1", email="admin1@example.com", password="secret123")
        self.assertEqual(admin.role, Role.ADMIN)
        self.assertTrue(admin.is_staff)
        self.assertTrue(admin.is_superuser)

    def test_str_returns_username(self) -> None:
        user = User.objects.create_user(
            username="comptable2", email="comptable2@example.com", password="secret123", role=Role.COMPTABLE
        )
        self.assertEqual(str(user), "comptable2")

    def test_date_desactivation_defaults_to_none(self) -> None:
        """RGPD — un compte fraîchement créé n'a jamais été désactivé."""
        user = User.objects.create_user(
            username="agent2", email="agent2@example.com", password="secret123", role=Role.AGENT
        )
        self.assertIsNone(user.date_desactivation)
