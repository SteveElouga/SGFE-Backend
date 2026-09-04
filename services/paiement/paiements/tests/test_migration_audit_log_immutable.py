"""Tests de la migration `0013_audit_log_immutable` (voir AUDIT_SGFE.md §10.7).

Les tests locaux par défaut tournent sur SQLite (`TESTING`, voir
`settings.py`) : cette migration s'y applique donc déjà comme un no-op à
chaque `manage.py test` (branche `vendor != "postgresql"`). Ce fichier
complète en testant directement les fonctions `RunPython`, avec un
`schema_editor` simulé, pour couvrir aussi la branche PostgreSQL (REVOKE/GRANT
réels, vérifiés manuellement contre un Postgres jetable — voir la PR) sans
dépendre d'un vrai serveur Postgres dans cette suite.
"""

from __future__ import annotations

import importlib
from unittest.mock import MagicMock

from django.test import SimpleTestCase

_migration = importlib.import_module("paiements.migrations.0013_audit_log_immutable")


class RoleCourantTests(SimpleTestCase):
    def test_none_hors_postgresql(self) -> None:
        schema_editor = MagicMock()
        schema_editor.connection.vendor = "sqlite"
        self.assertIsNone(_migration._role_courant(schema_editor))

    def test_none_si_aucun_utilisateur_configure(self) -> None:
        schema_editor = MagicMock()
        schema_editor.connection.vendor = "postgresql"
        schema_editor.connection.settings_dict = {"USER": ""}
        self.assertIsNone(_migration._role_courant(schema_editor))

    def test_renvoie_le_role_configure_sur_postgresql(self) -> None:
        schema_editor = MagicMock()
        schema_editor.connection.vendor = "postgresql"
        schema_editor.connection.settings_dict = {"USER": "paiement_user"}
        self.assertEqual(_migration._role_courant(schema_editor), "paiement_user")


class RevokeGrantTests(SimpleTestCase):
    def _schema_editor_postgres(self, role: str = "paiement_user") -> MagicMock:
        schema_editor = MagicMock()
        schema_editor.connection.vendor = "postgresql"
        schema_editor.connection.settings_dict = {"USER": role}
        return schema_editor

    def test_revoke_execute_le_sql_attendu_sur_postgresql(self) -> None:
        schema_editor = self._schema_editor_postgres()
        cursor = schema_editor.connection.cursor.return_value.__enter__.return_value

        _migration._revoke_update_delete(None, schema_editor)

        cursor.execute.assert_called_once_with('REVOKE UPDATE, DELETE ON audit_log FROM "paiement_user";')

    def test_grant_execute_le_sql_attendu_sur_postgresql(self) -> None:
        schema_editor = self._schema_editor_postgres()
        cursor = schema_editor.connection.cursor.return_value.__enter__.return_value

        _migration._grant_update_delete(None, schema_editor)

        cursor.execute.assert_called_once_with('GRANT UPDATE, DELETE ON audit_log TO "paiement_user";')

    def test_revoke_ne_fait_rien_hors_postgresql(self) -> None:
        schema_editor = MagicMock()
        schema_editor.connection.vendor = "sqlite"

        _migration._revoke_update_delete(None, schema_editor)

        schema_editor.connection.cursor.assert_not_called()

    def test_grant_ne_fait_rien_hors_postgresql(self) -> None:
        schema_editor = MagicMock()
        schema_editor.connection.vendor = "sqlite"

        _migration._grant_update_delete(None, schema_editor)

        schema_editor.connection.cursor.assert_not_called()


class MigrationOperationsTests(SimpleTestCase):
    def test_operation_wired_avec_forward_et_reverse(self) -> None:
        (operation,) = _migration.Migration.operations
        self.assertIs(operation.code, _migration._revoke_update_delete)
        self.assertIs(operation.reverse_code, _migration._grant_update_delete)

    def test_depend_de_la_migration_auditlog(self) -> None:
        self.assertIn(("paiements", "0012_auditlog"), _migration.Migration.dependencies)
