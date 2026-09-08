"""Tests de la migration de données `0012_encrypt_existing_email_phone_data`
— exécute la fonction `encrypt_existing_email_phone` directement contre des
lignes `users` insérées en SQL brut (contournant l'ORM, comme le ferait une
vraie base pré-existante à cette migration, où `email`/`phone_number` sont
encore en clair). Même esprit que `test_migration_audit_log_role_runtime.py`
pour l'import du module de migration (nom non-identifiant Python valide).

`phone_number_hash` reçoit un placeholder non-NULL à l'insertion : au moment
réel où cette migration s'exécute en production (juste après
0011_encrypt_email_phone_fields), la colonne est encore nullable
(`unique=True`/`null=False` n'arrivent qu'en 0013_alter_email_phone_hash_unique,
une fois les données backfillées) — mais le schéma de la base de test, lui,
est déjà au dernier état (toutes les migrations appliquées avant que les tests
ne s'exécutent), donc NOT NULL. Le placeholder simule fidèlement une valeur
« pas encore calculée » sans violer cette contrainte du schéma final."""

from __future__ import annotations

import importlib
import uuid
from datetime import UTC, datetime

from django.db import connection
from django.test import TestCase

from comptes import fields

_migration = importlib.import_module("comptes.migrations.0012_encrypt_existing_email_phone_data")


class _FakeSchemaEditor:
    """Assez d'un `schema_editor` pour cette migration : seul `.connection`
    est utilisé (`schema_editor.connection.cursor()`)."""

    connection = connection


def _insert_plaintext_user(username: str, email: str | None, phone_number: str) -> str:
    user_id = str(uuid.uuid4())
    now = datetime.now(UTC).isoformat()
    with connection.cursor() as cursor:
        cursor.execute(
            # is_superuser/is_active/is_staff sont de vraies colonnes booléennes sous
            # PostgreSQL (utilisé en CI, FORCE_POSTGRES_TESTS=True) : contrairement à
            # SQLite (affinité entière, 0/1 acceptés partout), Postgres refuse de
            # caster silencieusement un littéral entier en boolean dans un INSERT
            # (« column "is_superuser" is of type boolean but expression is of type
            # integer ») — d'où FALSE/TRUE plutôt que 0/1 ci-dessous.
            "INSERT INTO users (id, password, is_superuser, username, email, email_hash, phone_number, "
            "phone_number_hash, role, is_active, failed_attempts, is_staff, created_at, updated_at) VALUES "
            "(%s, '', FALSE, %s, %s, NULL, %s, %s, 'AGENT', TRUE, 0, FALSE, %s, %s)",
            [user_id, username, email, phone_number, f"placeholder-{username}", now, now],
        )
    return user_id


def _fetch_row(user_id: str) -> tuple[str | None, str | None, str, str]:
    with connection.cursor() as cursor:
        cursor.execute("SELECT email, email_hash, phone_number, phone_number_hash FROM users WHERE id = %s", [user_id])
        row: tuple[str | None, str | None, str, str] = cursor.fetchone()
        return row


class EncryptExistingEmailPhoneDataTests(TestCase):
    def test_chiffre_et_hash_une_ligne_en_clair(self) -> None:
        user_id = _insert_plaintext_user("migr_user1", "migr1@example.com", "+237690004001")

        _migration.encrypt_existing_email_phone(None, _FakeSchemaEditor())

        email_cipher, email_hash, phone_cipher, phone_hash = _fetch_row(user_id)
        assert email_cipher is not None  # narrowe le type pour mypy — nul déjà couvert par l'autre test
        self.assertNotEqual(email_cipher, "migr1@example.com")
        self.assertEqual(fields._fernet().decrypt(email_cipher.encode()).decode(), "migr1@example.com")
        self.assertEqual(email_hash, fields.hash_email("migr1@example.com"))
        self.assertNotEqual(phone_cipher, "+237690004001")
        self.assertEqual(fields._fernet().decrypt(phone_cipher.encode()).decode(), "+237690004001")
        self.assertEqual(phone_hash, fields.hash_phone("+237690004001"))

    def test_email_nul_reste_nul_sans_hash(self) -> None:
        user_id = _insert_plaintext_user("migr_user2", None, "+237690004002")

        _migration.encrypt_existing_email_phone(None, _FakeSchemaEditor())

        email_cipher, email_hash, _phone_cipher, _phone_hash = _fetch_row(user_id)
        self.assertIsNone(email_cipher)
        self.assertIsNone(email_hash)

    def test_idempotente_sur_une_ligne_deja_chiffree(self) -> None:
        """Rejouer la migration sur une ligne déjà chiffrée ne doit ni la
        casser (pas d'InvalidToken), ni changer son ciphertext/hash."""
        user_id = _insert_plaintext_user("migr_user3", "migr3@example.com", "+237690004003")
        _migration.encrypt_existing_email_phone(None, _FakeSchemaEditor())
        first_pass = _fetch_row(user_id)

        _migration.encrypt_existing_email_phone(None, _FakeSchemaEditor())
        second_pass = _fetch_row(user_id)

        self.assertEqual(first_pass, second_pass)


class MigrationOperationsTests(TestCase):
    def test_reverse_refuse(self) -> None:
        with self.assertRaises(RuntimeError):
            _migration.refuse_reverse(None, _FakeSchemaEditor())

    def test_depend_de_la_migration_de_schema(self) -> None:
        self.assertIn(("comptes", "0011_encrypt_email_phone_fields"), _migration.Migration.dependencies)
