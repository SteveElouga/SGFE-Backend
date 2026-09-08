"""Tests de `UserRepository` — en particulier les lookups par hash de
recherche déterministe (`email_hash`/`phone_number_hash`) qui remplacent les
lookups exacts sur `email`/`phone_number` en clair, désormais chiffrés
(voir comptes/fields.py)."""

from django.core.exceptions import ObjectDoesNotExist
from django.db import IntegrityError
from django.test import TestCase

from comptes.models import Role, User
from comptes.repositories import UserRepository


class GetByEmailTests(TestCase):
    def setUp(self) -> None:
        self.repo = UserRepository()
        self.user = User.objects.create_user(
            username="admin_email1", email="admin1@example.com", phone_number="+237690003001", role=Role.ADMIN
        )

    def test_trouve_par_email(self) -> None:
        found = self.repo.get_by_email("admin1@example.com")
        self.assertEqual(found.id, self.user.id)

    def test_email_inconnu_leve_object_does_not_exist(self) -> None:
        with self.assertRaises(ObjectDoesNotExist):
            self.repo.get_by_email("inconnu@example.com")


class GetByPhoneTests(TestCase):
    def setUp(self) -> None:
        self.repo = UserRepository()
        self.user = User.objects.create_user(username="agent_phone1", phone_number="+237690003002", role=Role.AGENT)

    def test_trouve_par_telephone(self) -> None:
        found = self.repo.get_by_phone("+237690003002")
        self.assertEqual(found.id, self.user.id)

    def test_telephone_inconnu_leve_object_does_not_exist(self) -> None:
        with self.assertRaises(ObjectDoesNotExist):
            self.repo.get_by_phone("+237690009999")


class GetByUsernameOrPhoneTests(TestCase):
    def setUp(self) -> None:
        self.repo = UserRepository()
        self.user = User.objects.create_user(username="login_user1", phone_number="+237690003003", role=Role.AGENT)

    def test_trouve_par_username(self) -> None:
        found = self.repo.get_by_username_or_phone("login_user1")
        self.assertEqual(found.id, self.user.id)

    def test_trouve_par_telephone(self) -> None:
        found = self.repo.get_by_username_or_phone("+237690003003")
        self.assertEqual(found.id, self.user.id)

    def test_identifiant_inconnu_leve_object_does_not_exist(self) -> None:
        with self.assertRaises(ObjectDoesNotExist):
            self.repo.get_by_username_or_phone("ni-username-ni-telephone")


class UniqueConstraintOnHashTests(TestCase):
    """La contrainte d'unicité, auparavant portée par `email`/`phone_number`
    en clair, est désormais portée par `email_hash`/`phone_number_hash`
    (voir comptes/models.py, comptes/fields.py) — elle doit continuer à
    empêcher deux comptes avec le même numéro/e-mail."""

    def test_meme_telephone_leve_integrity_error(self) -> None:
        User.objects.create_user(username="dup_phone1", phone_number="+237690003004", role=Role.AGENT)
        with self.assertRaises(IntegrityError):
            User.objects.create_user(username="dup_phone2", phone_number="+237690003004", role=Role.AGENT)

    def test_meme_email_leve_integrity_error(self) -> None:
        User.objects.create_user(
            username="dup_email1", email="dup@example.com", phone_number="+237690003005", role=Role.ADMIN
        )
        with self.assertRaises(IntegrityError):
            User.objects.create_user(
                username="dup_email2", email="dup@example.com", phone_number="+237690003006", role=Role.ADMIN
            )
