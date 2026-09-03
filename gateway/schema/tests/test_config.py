from unittest.mock import Mock, patch

from django.test import SimpleTestCase

from schema.grpc_clients import auth_client, config_client
from schema.schema import schema
from schema.tests.test_auth import _data, context


def make_infos_response(
    nom: str = "Eau SA", adresse: str = "Yaoundé", telephone: str = "+237699000000", logo_path: str = ""
) -> Mock:
    return Mock(nom=nom, adresse=adresse, telephone=telephone, logo_path=logo_path, updated_at="2024-01-01T00:00:00")


def make_config_response(
    cle: str = "DELAI_PAIEMENT_JOURS", valeur: str = "5", description: str = "Délai de paiement"
) -> Mock:
    return Mock(cle=cle, valeur=valeur, description=description)


def make_list_configs_response(*configs: Mock) -> Mock:
    return Mock(configs=list(configs))


class InfosSocieteQueryTests(SimpleTestCase):
    def test_infos_societe_returns_data_when_authenticated(self) -> None:
        with (
            patch.object(auth_client, "validate_token", return_value=Mock(user_id="u-1", role="COMPTABLE")),
            patch.object(config_client, "get_infos_societe", return_value=make_infos_response()),
        ):
            result = schema.execute_sync(
                "query { infosSociete { nom adresse telephone } }",
                context_value=context(token="access-1"),
            )

        self.assertIsNone(result.errors)
        self.assertEqual(_data(result)["infosSociete"]["nom"], "Eau SA")
        self.assertEqual(_data(result)["infosSociete"]["adresse"], "Yaoundé")

    def test_infos_societe_requires_auth(self) -> None:
        """Consommé uniquement par l'écran /configuration, derrière roleGuard(['ADMIN'])
        côté frontend (voir grep sur `infosSociete` dans SGFE-frontend/src) — aucun écran
        public (login, espace-abonné) n'en a besoin. L'exception documentée dans
        ARCHITECTURE.md/ETAT_DU_SYSTEME.md ne correspondait à aucun usage réel."""
        with patch.object(config_client, "get_infos_societe", return_value=make_infos_response()):
            result = schema.execute_sync(
                "query { infosSociete { nom } }",
                context_value=context(),
            )
        self.assertIsNotNone(result.errors)
        self.assertIn("Authentification requise", str(result.errors))


class ConfigQueryTests(SimpleTestCase):
    def test_config_requires_admin_role(self) -> None:
        with patch.object(auth_client, "validate_token", return_value=Mock(user_id="u-1", role="COMPTABLE")):
            result = schema.execute_sync(
                'query { config(cle: "DELAI_PAIEMENT_JOURS") { cle valeur } }',
                context_value=context(token="access-1"),
            )
        self.assertIsNotNone(result.errors)
        self.assertIn("Accès non autorisé", str(result.errors))

    def test_config_returns_param_as_admin(self) -> None:
        with (
            patch.object(auth_client, "validate_token", return_value=Mock(user_id="admin-1", role="ADMIN")),
            patch.object(config_client, "get_config", return_value=make_config_response()),
        ):
            result = schema.execute_sync(
                'query { config(cle: "DELAI_PAIEMENT_JOURS") { cle valeur description } }',
                context_value=context(token="access-1"),
            )
        self.assertIsNone(result.errors)
        self.assertEqual(_data(result)["config"]["cle"], "DELAI_PAIEMENT_JOURS")
        self.assertEqual(_data(result)["config"]["valeur"], "5")

    def test_configs_returns_list_as_admin(self) -> None:
        with (
            patch.object(auth_client, "validate_token", return_value=Mock(user_id="admin-1", role="ADMIN")),
            patch.object(
                config_client,
                "list_configs",
                return_value=make_list_configs_response(
                    make_config_response("DELAI_PAIEMENT_JOURS", "5"),
                    make_config_response("TOKEN_VALIDITE_JOURS", "20"),
                ),
            ),
        ):
            result = schema.execute_sync(
                "query { configs { cle valeur } }",
                context_value=context(token="access-1"),
            )
        self.assertIsNone(result.errors)
        self.assertEqual(len(_data(result)["configs"]), 2)

    def test_configs_requires_admin_role(self) -> None:
        with patch.object(auth_client, "validate_token", return_value=Mock(user_id="u-1", role="AGENT")):
            result = schema.execute_sync(
                "query { configs { cle } }",
                context_value=context(token="access-1"),
            )
        self.assertIsNotNone(result.errors)
        self.assertIn("Accès non autorisé", str(result.errors))


class ConfigMutationTests(SimpleTestCase):
    def test_update_infos_societe_requires_admin(self) -> None:
        with patch.object(auth_client, "validate_token", return_value=Mock(user_id="u-1", role="COMPTABLE")):
            result = schema.execute_sync(
                'mutation { updateInfosSociete(input: { nom: "Test" }) { nom } }',
                context_value=context(token="access-1"),
            )
        self.assertIsNotNone(result.errors)
        self.assertIn("Accès non autorisé", str(result.errors))

    def test_update_infos_societe_success_as_admin(self) -> None:
        with (
            patch.object(auth_client, "validate_token", return_value=Mock(user_id="admin-1", role="ADMIN")),
            patch.object(
                config_client,
                "update_infos_societe",
                return_value=make_infos_response(nom="SGFE Cameroun"),
            ),
        ):
            result = schema.execute_sync(
                'mutation { updateInfosSociete(input: { nom: "SGFE Cameroun" }) { nom } }',
                context_value=context(token="access-1"),
            )
        self.assertIsNone(result.errors)
        self.assertEqual(_data(result)["updateInfosSociete"]["nom"], "SGFE Cameroun")

    def test_update_config_requires_admin(self) -> None:
        with patch.object(auth_client, "validate_token", return_value=Mock(user_id="u-1", role="AGENT")):
            result = schema.execute_sync(
                'mutation { updateConfig(cle: "DELAI_PAIEMENT_JOURS", valeur: "10") { cle valeur } }',
                context_value=context(token="access-1"),
            )
        self.assertIsNotNone(result.errors)
        self.assertIn("Accès non autorisé", str(result.errors))

    def test_update_config_success_as_admin(self) -> None:
        with (
            patch.object(auth_client, "validate_token", return_value=Mock(user_id="admin-1", role="ADMIN")),
            patch.object(
                config_client,
                "update_config",
                return_value=make_config_response("DELAI_PAIEMENT_JOURS", "10"),
            ),
        ):
            result = schema.execute_sync(
                'mutation { updateConfig(cle: "DELAI_PAIEMENT_JOURS", valeur: "10") { cle valeur } }',
                context_value=context(token="access-1"),
            )
        self.assertIsNone(result.errors)
        self.assertEqual(_data(result)["updateConfig"]["valeur"], "10")
