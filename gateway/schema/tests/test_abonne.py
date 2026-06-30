from unittest.mock import Mock, patch

from django.test import SimpleTestCase

from schema.grpc_clients import abonne_client, auth_client
from schema.schema import schema
from schema.tests.test_auth import context


def make_compteur_response(numero_compteur=1, statut="ACTIF"):
    return Mock(
        compteur_id="compteur-1",
        numero_compteur=numero_compteur,
        quartier="Centre",
        camp=1,
        index_initial=0.0,
        date_pose="2024-01-01",
        statut=statut,
    )


def make_abonne_response(abonne_id="abonne-1", numero_abonne="AB-0001", statut="ACTIF", with_compteur=True):
    response = Mock(
        abonne_id=abonne_id,
        numero_abonne=numero_abonne,
        nom="Doe",
        prenom="John",
        telephone_whatsapp="+24100000000",
        adresse="Quartier X",
        statut=statut,
        created_at="2024-01-01T00:00:00",
    )
    response.compteur = make_compteur_response() if with_compteur else Mock()
    response.HasField = Mock(return_value=with_compteur)
    return response


def make_list_abonnes_response(*abonnes):
    return Mock(abonnes=list(abonnes))


class AbonneQueryTests(SimpleTestCase):
    def _admin_context(self):
        return context(token="access-1")

    def test_abonne_returns_abonne_with_compteur(self):
        with (
            patch.object(auth_client, "validate_token", return_value=Mock(user_id="admin-1", role="ADMIN")),
            patch.object(abonne_client, "get_abonne", return_value=make_abonne_response()),
        ):
            result = schema.execute_sync(
                'query { abonne(id: "abonne-1") { numeroAbonne compteur { numeroCompteur } } }',
                context_value=self._admin_context(),
            )

        self.assertIsNone(result.errors)
        self.assertEqual(result.data["abonne"]["numeroAbonne"], "AB-0001")
        self.assertEqual(result.data["abonne"]["compteur"]["numeroCompteur"], 1)

    def test_abonnes_lists_all(self):
        with (
            patch.object(auth_client, "validate_token", return_value=Mock(user_id="admin-1", role="ADMIN")),
            patch.object(
                abonne_client,
                "list_abonnes",
                return_value=make_list_abonnes_response(
                    make_abonne_response(), make_abonne_response(abonne_id="abonne-2")
                ),
            ),
        ):
            result = schema.execute_sync("query { abonnes { numeroAbonne } }", context_value=self._admin_context())

        self.assertIsNone(result.errors)
        self.assertEqual(len(result.data["abonnes"]), 2)

    def test_abonnes_filters_by_statut(self):
        with (
            patch.object(auth_client, "validate_token", return_value=Mock(user_id="admin-1", role="ADMIN")),
            patch.object(abonne_client, "list_abonnes", return_value=make_list_abonnes_response()) as mock_list,
        ):
            schema.execute_sync(
                "query { abonnes(statut: SUSPENDU) { numeroAbonne } }", context_value=self._admin_context()
            )
            mock_list.assert_called_once_with("SUSPENDU")


class AbonneMutationTests(SimpleTestCase):
    def _admin_context(self):
        return context(token="access-1")

    def test_create_abonne_requires_admin_role(self):
        with patch.object(auth_client, "validate_token", return_value=Mock(user_id="user-1", role="AGENT")):
            result = schema.execute_sync(
                'mutation { createAbonne(input: {nom: "Doe", prenom: "John", telephoneWhatsapp: "+241", '
                'numeroCompteur: 1, quartier: "Centre", camp: 1, indexInitial: 0, datePose: "2024-01-01"}) '
                "{ numeroAbonne } }",
                context_value=self._admin_context(),
            )

        self.assertIsNotNone(result.errors)
        self.assertIn("Accès non autorisé", str(result.errors))

    def test_create_abonne_success_as_admin(self):
        with (
            patch.object(auth_client, "validate_token", return_value=Mock(user_id="admin-1", role="ADMIN")),
            patch.object(abonne_client, "create_abonne", return_value=make_abonne_response()) as mock_create,
        ):
            result = schema.execute_sync(
                'mutation { createAbonne(input: {nom: "Doe", prenom: "John", telephoneWhatsapp: "+241", '
                'numeroCompteur: 1, quartier: "Centre", camp: 1, indexInitial: 0, datePose: "2024-01-01"}) '
                "{ numeroAbonne } }",
                context_value=self._admin_context(),
            )
            mock_create.assert_called_once()

        self.assertIsNone(result.errors)
        self.assertEqual(result.data["createAbonne"]["numeroAbonne"], "AB-0001")

    def test_update_abonne_success_as_admin(self):
        with (
            patch.object(auth_client, "validate_token", return_value=Mock(user_id="admin-1", role="ADMIN")),
            patch.object(abonne_client, "update_abonne", return_value=make_abonne_response(numero_abonne="AB-0002")),
        ):
            result = schema.execute_sync(
                'mutation { updateAbonne(id: "abonne-1", input: {nom: "Smith"}) { numeroAbonne } }',
                context_value=self._admin_context(),
            )

        self.assertIsNone(result.errors)
        self.assertEqual(result.data["updateAbonne"]["numeroAbonne"], "AB-0002")

    def test_suspendre_abonne_success_as_admin(self):
        with (
            patch.object(auth_client, "validate_token", return_value=Mock(user_id="admin-1", role="ADMIN")),
            patch.object(abonne_client, "suspendre_abonne", return_value=make_abonne_response(statut="SUSPENDU")),
        ):
            result = schema.execute_sync(
                'mutation { suspendreAbonne(id: "abonne-1") { statut } }', context_value=self._admin_context()
            )

        self.assertIsNone(result.errors)
        self.assertEqual(result.data["suspendreAbonne"]["statut"], "SUSPENDU")

    def test_reactiver_abonne_success_as_admin(self):
        with (
            patch.object(auth_client, "validate_token", return_value=Mock(user_id="admin-1", role="ADMIN")),
            patch.object(abonne_client, "reactiver_abonne", return_value=make_abonne_response(statut="ACTIF")),
        ):
            result = schema.execute_sync(
                'mutation { reactiverAbonne(id: "abonne-1") { statut } }', context_value=self._admin_context()
            )

        self.assertIsNone(result.errors)
        self.assertEqual(result.data["reactiverAbonne"]["statut"], "ACTIF")

    def test_update_compteur_success_as_admin(self):
        with (
            patch.object(auth_client, "validate_token", return_value=Mock(user_id="admin-1", role="ADMIN")),
            patch.object(abonne_client, "update_compteur", return_value=make_compteur_response()) as mock_update,
        ):
            result = schema.execute_sync(
                'mutation { updateCompteur(abonneId: "abonne-1", input: { quartier: "Bastos", camp: 2 }) '
                "{ quartier camp } }",
                context_value=self._admin_context(),
            )
            mock_update.assert_called_once_with("abonne-1", quartier="Bastos", camp=2)

        self.assertIsNone(result.errors)

    def test_update_compteur_requires_admin_role(self):
        with patch.object(auth_client, "validate_token", return_value=Mock(user_id="user-1", role="AGENT")):
            result = schema.execute_sync(
                'mutation { updateCompteur(abonneId: "abonne-1", input: { quartier: "X" }) { quartier } }',
                context_value=self._admin_context(),
            )
        self.assertIsNotNone(result.errors)
        self.assertIn("Accès non autorisé", str(result.errors))

    def test_resilier_abonne_success_as_admin(self):
        with (
            patch.object(auth_client, "validate_token", return_value=Mock(user_id="admin-1", role="ADMIN")),
            patch.object(abonne_client, "resilier_abonne", return_value=make_abonne_response(statut="RESILIE")),
        ):
            result = schema.execute_sync(
                'mutation { resilierAbonne(id: "abonne-1") { statut } }', context_value=self._admin_context()
            )

        self.assertIsNone(result.errors)
        self.assertEqual(result.data["resilierAbonne"]["statut"], "RESILIE")

    def test_resilier_abonne_requires_admin_role(self):
        with patch.object(auth_client, "validate_token", return_value=Mock(user_id="user-1", role="COMPTABLE")):
            result = schema.execute_sync(
                'mutation { resilierAbonne(id: "abonne-1") { statut } }', context_value=self._admin_context()
            )

        self.assertIsNotNone(result.errors)
        self.assertIn("Accès non autorisé", str(result.errors))

    def test_remplacer_compteur_requires_admin_role(self):
        with patch.object(auth_client, "validate_token", return_value=Mock(user_id="user-1", role="COMPTABLE")):
            result = schema.execute_sync(
                'mutation { remplacerCompteur(abonneId: "abonne-1", input: {indexFermeture: 100, '
                'nouveauNumeroCompteur: 2, nouveauQuartier: "Q", nouveauCamp: 2, nouvelIndexInitial: 0, '
                'dateRemplacement: "2024-06-01"}) { numeroCompteur } }',
                context_value=self._admin_context(),
            )

        self.assertIsNotNone(result.errors)
        self.assertIn("Accès non autorisé", str(result.errors))

    def test_remplacer_compteur_success_as_admin(self):
        with (
            patch.object(auth_client, "validate_token", return_value=Mock(user_id="admin-1", role="ADMIN")),
            patch.object(abonne_client, "remplacer_compteur", return_value=make_compteur_response(numero_compteur=2)),
        ):
            result = schema.execute_sync(
                'mutation { remplacerCompteur(abonneId: "abonne-1", input: {indexFermeture: 100, '
                'nouveauNumeroCompteur: 2, nouveauQuartier: "Q", nouveauCamp: 2, nouvelIndexInitial: 0, '
                'dateRemplacement: "2024-06-01"}) { numeroCompteur } }',
                context_value=self._admin_context(),
            )

        self.assertIsNone(result.errors)
        self.assertEqual(result.data["remplacerCompteur"]["numeroCompteur"], 2)


class AbonnesActifsQueryTests(SimpleTestCase):
    def test_abonnes_actifs_returns_list(self):
        with (
            patch.object(auth_client, "validate_token", return_value=Mock(user_id="admin-1", role="ADMIN")),
            patch.object(
                abonne_client,
                "list_abonnes_actifs",
                return_value=make_list_abonnes_response(
                    make_abonne_response("abonne-1", "AB-0001", "ACTIF"),
                    make_abonne_response("abonne-2", "AB-0002", "ACTIF"),
                ),
            ),
        ):
            result = schema.execute_sync(
                "query { abonnesActifs { id numeroAbonne statut } }",
                context_value=context(token="access-1"),
            )

        self.assertIsNone(result.errors)
        self.assertEqual(len(result.data["abonnesActifs"]), 2)
        self.assertEqual(result.data["abonnesActifs"][0]["statut"], "ACTIF")


class HistoriqueCompteurQueryTests(SimpleTestCase):
    def _make_historique_response(self):
        h = Mock()
        h.historique_id = "histo-1"
        h.ancien_compteur = make_compteur_response(numero_compteur=1, statut="REMPLACE")
        h.nouveau_compteur = make_compteur_response(numero_compteur=2, statut="ACTIF")
        h.index_fermeture = 120.0
        h.date_remplacement = "2024-06-01"
        h.created_at = "2024-06-01T08:00:00"
        return h

    def test_historique_compteur_returns_list(self):
        with (
            patch.object(auth_client, "validate_token", return_value=Mock(user_id="admin-1", role="ADMIN")),
            patch.object(
                abonne_client,
                "get_historique_compteur",
                return_value=Mock(historique=[self._make_historique_response()]),
            ),
        ):
            result = schema.execute_sync(
                'query { historiqueCompteur(id: "abonne-1") { id indexFermeture '
                "ancienCompteur { numeroCompteur statut } nouveauCompteur { numeroCompteur } } }",
                context_value=context(token="access-1"),
            )

        self.assertIsNone(result.errors)
        self.assertEqual(len(result.data["historiqueCompteur"]), 1)
        entry = result.data["historiqueCompteur"][0]
        self.assertEqual(entry["indexFermeture"], 120.0)
        self.assertEqual(entry["ancienCompteur"]["numeroCompteur"], 1)
        self.assertEqual(entry["ancienCompteur"]["statut"], "REMPLACE")
        self.assertEqual(entry["nouveauCompteur"]["numeroCompteur"], 2)

    def test_historique_compteur_empty_returns_empty_list(self):
        with (
            patch.object(auth_client, "validate_token", return_value=Mock(user_id="admin-1", role="ADMIN")),
            patch.object(
                abonne_client,
                "get_historique_compteur",
                return_value=Mock(historique=[]),
            ),
        ):
            result = schema.execute_sync(
                'query { historiqueCompteur(id: "abonne-1") { id } }',
                context_value=context(token="access-1"),
            )

        self.assertIsNone(result.errors)
        self.assertEqual(result.data["historiqueCompteur"], [])
