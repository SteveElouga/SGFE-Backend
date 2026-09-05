"""Tests de la migration `0010_audit_log_role_runtime` (voir AUDIT_SGFE.md
§8·J et `factures/db_hardening.py`).

La génération SQL elle-même est déjà couverte par `test_db_hardening.py` ;
ce fichier vérifie seulement que la migration délègue correctement, avec la
bonne table et le bon sens forward/reverse — même style que
`test_migration_audit_log_immutable.py`.
"""

from __future__ import annotations

import importlib
from unittest.mock import MagicMock, patch

from django.test import SimpleTestCase

_migration = importlib.import_module("factures.migrations.0010_audit_log_role_runtime")


class MigrationDelegationTests(SimpleTestCase):
    def test_forward_delegue_a_creer_role_runtime_avec_audit_log(self) -> None:
        schema_editor = MagicMock()
        with patch.object(_migration, "creer_role_runtime_et_isoler_table") as mock_creer:
            _migration._isoler_audit_log(None, schema_editor)
        mock_creer.assert_called_once_with(schema_editor, tables_immuables=("audit_log",))

    def test_reverse_delegue_a_supprimer_role_runtime_avec_audit_log(self) -> None:
        schema_editor = MagicMock()
        with patch.object(_migration, "supprimer_role_runtime") as mock_supprimer:
            _migration._desisoler_audit_log(None, schema_editor)
        mock_supprimer.assert_called_once_with(schema_editor, tables_immuables=("audit_log",))


class MigrationOperationsTests(SimpleTestCase):
    def test_operation_wired_avec_forward_et_reverse(self) -> None:
        (operation,) = _migration.Migration.operations
        self.assertIs(operation.code, _migration._isoler_audit_log)
        self.assertIs(operation.reverse_code, _migration._desisoler_audit_log)

    def test_depend_de_la_derniere_migration(self) -> None:
        self.assertIn(("factures", "0009_outboxevent"), _migration.Migration.dependencies)
