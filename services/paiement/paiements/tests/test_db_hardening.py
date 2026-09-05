"""Tests de `paiements/db_hardening.py` (voir AUDIT_SGFE.md §8·J).

Comme `test_migration_audit_log_immutable.py`, ces tests couvrent la
génération SQL avec une connexion/un `schema_editor` simulés — le
comportement Postgres réel (permission denied effective) est prouvé par
`test_db_hardening_postgres.py`, gaté par `FORCE_POSTGRES_TESTS` (voir ce
fichier pour le détail, et la CI qui le fait déjà tourner contre un
`postgres:16-alpine` jetable).
"""

from __future__ import annotations

import sys
from unittest.mock import MagicMock, patch

from django.db.backends.signals import connection_created
from django.test import SimpleTestCase

from paiements import db_hardening as dh

_TABLE = "audit_log"


class RoleProprietaireTests(SimpleTestCase):
    def test_none_hors_postgresql(self) -> None:
        connexion = MagicMock()
        connexion.vendor = "sqlite"
        self.assertIsNone(dh.role_proprietaire(connexion))

    def test_none_si_aucun_utilisateur_configure(self) -> None:
        connexion = MagicMock()
        connexion.vendor = "postgresql"
        connexion.settings_dict = {"USER": ""}
        self.assertIsNone(dh.role_proprietaire(connexion))

    def test_renvoie_le_role_configure_sur_postgresql(self) -> None:
        connexion = MagicMock()
        connexion.vendor = "postgresql"
        connexion.settings_dict = {"USER": "paiement_user"}
        self.assertEqual(dh.role_proprietaire(connexion), "paiement_user")


class RoleRuntimeTests(SimpleTestCase):
    def test_none_hors_postgresql(self) -> None:
        connexion = MagicMock()
        connexion.vendor = "sqlite"
        self.assertIsNone(dh.role_runtime(connexion))

    def test_suffixe_runtime_derive_du_role_proprietaire(self) -> None:
        connexion = MagicMock()
        connexion.vendor = "postgresql"
        connexion.settings_dict = {"USER": "paiement_user"}
        self.assertEqual(dh.role_runtime(connexion), "paiement_user_runtime")


class CreerRoleRuntimeTests(SimpleTestCase):
    def _schema_editor_postgres(self, role: str = "paiement_user") -> MagicMock:
        schema_editor = MagicMock()
        schema_editor.connection.vendor = "postgresql"
        schema_editor.connection.settings_dict = {"USER": role}
        return schema_editor

    def _cursor(self, schema_editor: MagicMock) -> MagicMock:
        cursor: MagicMock = schema_editor.connection.cursor.return_value.__enter__.return_value
        return cursor

    def test_ne_fait_rien_hors_postgresql(self) -> None:
        schema_editor = MagicMock()
        schema_editor.connection.vendor = "sqlite"

        dh.creer_role_runtime_et_isoler_table(schema_editor, tables_immuables=(_TABLE,))

        schema_editor.connection.cursor.assert_not_called()

    def test_cree_le_role_quand_absent_et_isole_la_table(self) -> None:
        schema_editor = self._schema_editor_postgres()
        cursor = self._cursor(schema_editor)
        cursor.fetchone.return_value = None  # le rôle _runtime n'existe pas encore

        dh.creer_role_runtime_et_isoler_table(schema_editor, tables_immuables=(_TABLE,))

        cursor.execute.assert_any_call('CREATE ROLE "paiement_user_runtime" NOLOGIN;')
        cursor.execute.assert_any_call('GRANT "paiement_user_runtime" TO "paiement_user";')
        cursor.execute.assert_any_call('GRANT USAGE ON SCHEMA public TO "paiement_user_runtime";')
        cursor.execute.assert_any_call(
            'GRANT ALL PRIVILEGES ON ALL TABLES IN SCHEMA public TO "paiement_user_runtime";'
        )
        cursor.execute.assert_any_call(
            'GRANT ALL PRIVILEGES ON ALL SEQUENCES IN SCHEMA public TO "paiement_user_runtime";'
        )
        cursor.execute.assert_any_call(
            'ALTER DEFAULT PRIVILEGES FOR ROLE "paiement_user" IN SCHEMA public '
            'GRANT ALL ON TABLES TO "paiement_user_runtime";'
        )
        cursor.execute.assert_any_call(
            'ALTER DEFAULT PRIVILEGES FOR ROLE "paiement_user" IN SCHEMA public '
            'GRANT ALL ON SEQUENCES TO "paiement_user_runtime";'
        )
        cursor.execute.assert_any_call('REVOKE UPDATE, DELETE ON "audit_log" FROM "paiement_user_runtime";')

    def test_ne_recree_pas_le_role_quand_deja_present(self) -> None:
        schema_editor = self._schema_editor_postgres()
        cursor = self._cursor(schema_editor)
        cursor.fetchone.return_value = (1,)  # le rôle _runtime existe déjà

        dh.creer_role_runtime_et_isoler_table(schema_editor, tables_immuables=(_TABLE,))

        for executed in cursor.execute.call_args_list:
            self.assertNotIn("CREATE ROLE", executed.args[0])
        # Les GRANT/ALTER DEFAULT PRIVILEGES restent réappliqués (idempotent).
        cursor.execute.assert_any_call('GRANT USAGE ON SCHEMA public TO "paiement_user_runtime";')

    def test_isole_plusieurs_tables(self) -> None:
        schema_editor = self._schema_editor_postgres()
        cursor = self._cursor(schema_editor)
        cursor.fetchone.return_value = (1,)

        dh.creer_role_runtime_et_isoler_table(schema_editor, tables_immuables=("audit_log", "autre_table"))

        cursor.execute.assert_any_call('REVOKE UPDATE, DELETE ON "audit_log" FROM "paiement_user_runtime";')
        cursor.execute.assert_any_call('REVOKE UPDATE, DELETE ON "autre_table" FROM "paiement_user_runtime";')


class SupprimerRoleRuntimeTests(SimpleTestCase):
    def _schema_editor_postgres(self, role: str = "paiement_user") -> MagicMock:
        schema_editor = MagicMock()
        schema_editor.connection.vendor = "postgresql"
        schema_editor.connection.settings_dict = {"USER": role}
        return schema_editor

    def _cursor(self, schema_editor: MagicMock) -> MagicMock:
        cursor: MagicMock = schema_editor.connection.cursor.return_value.__enter__.return_value
        return cursor

    def test_ne_fait_rien_hors_postgresql(self) -> None:
        schema_editor = MagicMock()
        schema_editor.connection.vendor = "sqlite"

        dh.supprimer_role_runtime(schema_editor, tables_immuables=(_TABLE,))

        schema_editor.connection.cursor.assert_not_called()

    def test_ne_fait_rien_si_le_role_runtime_n_existe_pas(self) -> None:
        schema_editor = self._schema_editor_postgres()
        cursor = self._cursor(schema_editor)
        cursor.fetchone.return_value = None

        dh.supprimer_role_runtime(schema_editor, tables_immuables=(_TABLE,))

        # Seule la vérification d'existence a été exécutée, rien d'autre.
        self.assertEqual(cursor.execute.call_count, 1)

    def test_retire_les_droits_et_supprime_le_role_quand_present(self) -> None:
        schema_editor = self._schema_editor_postgres()
        cursor = self._cursor(schema_editor)
        cursor.fetchone.return_value = (1,)

        dh.supprimer_role_runtime(schema_editor, tables_immuables=(_TABLE,))

        cursor.execute.assert_any_call('GRANT UPDATE, DELETE ON "audit_log" TO "paiement_user_runtime";')
        cursor.execute.assert_any_call('REVOKE "paiement_user_runtime" FROM "paiement_user";')
        cursor.execute.assert_any_call('DROP OWNED BY "paiement_user_runtime";')
        cursor.execute.assert_any_call('DROP ROLE IF EXISTS "paiement_user_runtime";')


class DoitGarderRoleProprietaireTests(SimpleTestCase):
    def test_true_pour_migrate(self) -> None:
        with patch.object(sys, "argv", ["manage.py", "migrate"]):
            self.assertTrue(dh._doit_garder_role_proprietaire())

    def test_true_pour_test(self) -> None:
        with patch.object(sys, "argv", ["manage.py", "test", "paiements"]):
            self.assertTrue(dh._doit_garder_role_proprietaire())

    def test_false_pour_grpc_server(self) -> None:
        with patch.object(sys, "argv", ["manage.py", "grpc_server"]):
            self.assertFalse(dh._doit_garder_role_proprietaire())

    def test_false_sans_sous_commande(self) -> None:
        with patch.object(sys, "argv", ["manage.py"]):
            self.assertFalse(dh._doit_garder_role_proprietaire())


class ActiverIsolementRuntimeTests(SimpleTestCase):
    def test_ne_fait_rien_hors_postgresql(self) -> None:
        connexion = MagicMock()
        connexion.vendor = "sqlite"

        with patch.object(sys, "argv", ["manage.py", "grpc_server"]):
            dh.activer_isolement_runtime(sender=object(), connection=connexion)

        connexion.cursor.assert_not_called()

    def test_ne_fait_rien_pour_migrate(self) -> None:
        connexion = MagicMock()
        connexion.vendor = "postgresql"
        connexion.settings_dict = {"USER": "paiement_user"}

        with patch.object(sys, "argv", ["manage.py", "migrate"]):
            dh.activer_isolement_runtime(sender=object(), connection=connexion)

        connexion.cursor.assert_not_called()

    def test_bascule_sur_le_role_runtime_pour_grpc_server(self) -> None:
        connexion = MagicMock()
        connexion.vendor = "postgresql"
        connexion.settings_dict = {"USER": "paiement_user"}
        cursor = connexion.cursor.return_value.__enter__.return_value

        with patch.object(sys, "argv", ["manage.py", "grpc_server"]):
            dh.activer_isolement_runtime(sender=object(), connection=connexion)

        cursor.execute.assert_called_once_with('SET ROLE "paiement_user_runtime";')


class ConnecterIsolementRuntimeTests(SimpleTestCase):
    def test_connecte_le_receiver_au_signal(self) -> None:
        # `connection_created` est un objet `Signal` unique côté Django : le
        # patcher via son module d'origine plutôt que via `dh.connection_created`
        # cible le même objet (évite un avertissement mypy --strict de
        # ré-export implicite) et affecte `connecter_isolement_runtime` de la
        # même façon, puisque c'est la même instance.
        with patch.object(connection_created, "connect") as connect_mock:
            dh.connecter_isolement_runtime()

        connect_mock.assert_called_once_with(dh.activer_isolement_runtime, dispatch_uid="sgfe_common.db_hardening")


class ModuleWiringTests(SimpleTestCase):
    def test_commandes_role_proprietaire_couvre_migrate_et_test(self) -> None:
        self.assertEqual(
            dh.COMMANDES_ROLE_PROPRIETAIRE,
            frozenset({"migrate", "makemigrations", "sqlmigrate", "test"}),
        )
