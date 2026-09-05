from typing import Any
from unittest.mock import Mock, patch

from django.test import SimpleTestCase

from schema.grpc_clients import abonne_client, auth_client
from schema.schema import schema
from schema.tests.test_auth import _data, context


def make_compteur_response(numero_compteur: int = 1, statut: str = "ACTIF", position: str = "") -> Mock:
    return Mock(
        compteur_id="compteur-1",
        numero_compteur=numero_compteur,
        quartier="Centre",
        camp=1,
        index_initial=0.0,
        date_pose="2024-01-01",
        statut=statut,
        position=position,
    )


def make_abonne_response(
    abonne_id: str = "abonne-1",
    numero_abonne: str = "AB-0001",
    statut: str = "ACTIF",
    with_compteur: bool = True,
    nom: str = "Doe",
) -> Mock:
    response = Mock(
        abonne_id=abonne_id,
        numero_abonne=numero_abonne,
        nom=nom,
        prenom="John",
        telephone_whatsapp="+24100000000",
        adresse="Quartier X",
        statut=statut,
        created_at="2024-01-01T00:00:00",
    )
    response.compteur = make_compteur_response() if with_compteur else Mock()
    response.HasField = Mock(return_value=with_compteur)
    return response


def make_list_abonnes_response(*abonnes: Mock) -> Mock:
    return Mock(abonnes=list(abonnes))


class AbonneQueryTests(SimpleTestCase):
    def _admin_context(self) -> dict[str, Any]:
        return context(token="access-1")

    def test_abonne_returns_abonne_with_compteur(self) -> None:
        with (
            patch.object(auth_client, "validate_token", return_value=Mock(user_id="admin-1", role="ADMIN")),
            patch.object(abonne_client, "get_abonne", return_value=make_abonne_response()),
        ):
            result = schema.execute_sync(
                'query { abonne(id: "abonne-1") { numeroAbonne compteur { numeroCompteur } } }',
                context_value=self._admin_context(),
            )

        self.assertIsNone(result.errors)
        self.assertEqual(_data(result)["abonne"]["numeroAbonne"], "AB-0001")
        self.assertEqual(_data(result)["abonne"]["compteur"]["numeroCompteur"], 1)

    def test_abonnes_lists_all(self) -> None:
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
        self.assertEqual(len(_data(result)["abonnes"]), 2)

    def test_abonnes_filters_by_statut(self) -> None:
        with (
            patch.object(auth_client, "validate_token", return_value=Mock(user_id="admin-1", role="ADMIN")),
            patch.object(abonne_client, "list_abonnes", return_value=make_list_abonnes_response()) as mock_list,
        ):
            schema.execute_sync(
                "query { abonnes(statut: SUSPENDU) { numeroAbonne } }", context_value=self._admin_context()
            )
            mock_list.assert_called_once_with("SUSPENDU")

    def test_abonnes_sans_limit_offset_renvoie_tout_comme_avant(self) -> None:
        """Non-régression explicite : une requête sans `limit`/`offset`
        continue de tout renvoyer, et le client gRPC est appelé exactement
        comme avant leur introduction (rétrocompatibilité stricte)."""
        with (
            patch.object(auth_client, "validate_token", return_value=Mock(user_id="admin-1", role="ADMIN")),
            patch.object(
                abonne_client,
                "list_abonnes",
                return_value=make_list_abonnes_response(
                    make_abonne_response(), make_abonne_response(abonne_id="abonne-2")
                ),
            ) as mock_list,
        ):
            result = schema.execute_sync("query { abonnes { numeroAbonne } }", context_value=self._admin_context())

        self.assertIsNone(result.errors)
        self.assertEqual(len(_data(result)["abonnes"]), 2)
        mock_list.assert_called_once_with("")

    def test_abonnes_avec_pagination_transmet_limit_offset(self) -> None:
        with (
            patch.object(auth_client, "validate_token", return_value=Mock(user_id="admin-1", role="ADMIN")),
            patch.object(abonne_client, "list_abonnes", return_value=make_list_abonnes_response()) as mock_list,
        ):
            result = schema.execute_sync(
                "query { abonnes(limit: 5, offset: 10) { numeroAbonne } }", context_value=self._admin_context()
            )
            mock_list.assert_called_once_with("", limit=5, offset=10)

        self.assertIsNone(result.errors)

    def test_abonnes_count(self) -> None:
        with (
            patch.object(auth_client, "validate_token", return_value=Mock(user_id="admin-1", role="ADMIN")),
            patch.object(abonne_client, "count_abonnes", return_value=42) as mock_count,
        ):
            result = schema.execute_sync(
                "query { abonnesCount(statut: SUSPENDU) }", context_value=self._admin_context()
            )
            mock_count.assert_called_once_with("SUSPENDU")

        self.assertIsNone(result.errors)
        self.assertEqual(_data(result)["abonnesCount"], 42)


class AbonneMutationTests(SimpleTestCase):
    def _admin_context(self) -> dict[str, Any]:
        return context(token="access-1")

    def test_create_abonne_requires_admin_role(self) -> None:
        with patch.object(auth_client, "validate_token", return_value=Mock(user_id="user-1", role="AGENT")):
            result = schema.execute_sync(
                'mutation { createAbonne(input: {nom: "Doe", prenom: "John", telephoneWhatsapp: "+24100000000", '
                'numeroCompteur: 1, quartier: "Centre", camp: 1, indexInitial: 0, datePose: "2024-01-01"}) '
                "{ numeroAbonne } }",
                context_value=self._admin_context(),
            )

        self.assertIsNotNone(result.errors)
        self.assertIn("Accès non autorisé", str(result.errors))

    def test_create_abonne_success_as_admin(self) -> None:
        with (
            patch.object(auth_client, "validate_token", return_value=Mock(user_id="admin-1", role="ADMIN")),
            patch.object(abonne_client, "create_abonne", return_value=make_abonne_response()) as mock_create,
        ):
            result = schema.execute_sync(
                'mutation { createAbonne(input: {nom: "Doe", prenom: "John", telephoneWhatsapp: "+24100000000", '
                'numeroCompteur: 1, quartier: "Centre", camp: 1, indexInitial: 0, datePose: "2024-01-01"}) '
                "{ numeroAbonne } }",
                context_value=self._admin_context(),
            )
            mock_create.assert_called_once()

        self.assertIsNone(result.errors)
        self.assertEqual(_data(result)["createAbonne"]["numeroAbonne"], "AB-0001")

    def test_create_abonne_index_initial_negatif_rejete_sans_appel_grpc(self) -> None:
        """Item #10 (ASVS V2) : un index négatif est rejeté à la gateway,
        avant tout appel gRPC vers l'Abonné Service."""
        with (
            patch.object(auth_client, "validate_token", return_value=Mock(user_id="admin-1", role="ADMIN")),
            patch.object(abonne_client, "create_abonne", return_value=make_abonne_response()) as mock_create,
        ):
            result = schema.execute_sync(
                'mutation { createAbonne(input: {nom: "Doe", prenom: "John", telephoneWhatsapp: "+24100000000", '
                'numeroCompteur: 1, quartier: "Centre", camp: 1, indexInitial: -5, datePose: "2024-01-01"}) '
                "{ numeroAbonne } }",
                context_value=self._admin_context(),
            )

        self.assertIsNotNone(result.errors)
        self.assertIn("index_initial doit être positif ou nul", str(result.errors))
        mock_create.assert_not_called()

    def test_create_abonne_telephone_invalide_rejete_sans_appel_grpc(self) -> None:
        """Item #10 (ASVS V2) : un numéro WhatsApp mal formé est rejeté à la
        gateway, avant tout appel gRPC vers l'Abonné Service."""
        with (
            patch.object(auth_client, "validate_token", return_value=Mock(user_id="admin-1", role="ADMIN")),
            patch.object(abonne_client, "create_abonne", return_value=make_abonne_response()) as mock_create,
        ):
            result = schema.execute_sync(
                'mutation { createAbonne(input: {nom: "Doe", prenom: "John", telephoneWhatsapp: "pas-un-numero", '
                'numeroCompteur: 1, quartier: "Centre", camp: 1, indexInitial: 0, datePose: "2024-01-01"}) '
                "{ numeroAbonne } }",
                context_value=self._admin_context(),
            )

        self.assertIsNotNone(result.errors)
        self.assertIn("telephone_whatsapp invalide", str(result.errors))
        mock_create.assert_not_called()

    def test_create_abonne_date_pose_invalide_rejetee_sans_appel_grpc(self) -> None:
        """Item #10 (ASVS V2) : une date mal formée est rejetée à la gateway,
        avant tout appel gRPC vers l'Abonné Service."""
        with (
            patch.object(auth_client, "validate_token", return_value=Mock(user_id="admin-1", role="ADMIN")),
            patch.object(abonne_client, "create_abonne", return_value=make_abonne_response()) as mock_create,
        ):
            result = schema.execute_sync(
                'mutation { createAbonne(input: {nom: "Doe", prenom: "John", telephoneWhatsapp: "+24100000000", '
                'numeroCompteur: 1, quartier: "Centre", camp: 1, indexInitial: 0, datePose: "pas-une-date"}) '
                "{ numeroAbonne } }",
                context_value=self._admin_context(),
            )

        self.assertIsNotNone(result.errors)
        self.assertIn("date_pose invalide", str(result.errors))
        mock_create.assert_not_called()

    def test_create_abonne_transporte_la_position(self) -> None:
        with (
            patch.object(auth_client, "validate_token", return_value=Mock(user_id="admin-1", role="ADMIN")),
            patch.object(abonne_client, "create_abonne", return_value=make_abonne_response()) as mock_create,
        ):
            schema.execute_sync(
                'mutation { createAbonne(input: {nom: "Doe", prenom: "John", telephoneWhatsapp: "+24100000000", '
                'numeroCompteur: 1, quartier: "Centre", camp: 1, indexInitial: 0, datePose: "2024-01-01", '
                'position: "3e maison à gauche"}) { numeroAbonne } }',
                context_value=self._admin_context(),
            )
            self.assertEqual(mock_create.call_args.kwargs["position"], "3e maison à gauche")

    def test_create_abonne_position_absente_transporte_chaine_vide(self) -> None:
        with (
            patch.object(auth_client, "validate_token", return_value=Mock(user_id="admin-1", role="ADMIN")),
            patch.object(abonne_client, "create_abonne", return_value=make_abonne_response()) as mock_create,
        ):
            schema.execute_sync(
                'mutation { createAbonne(input: {nom: "Doe", prenom: "John", telephoneWhatsapp: "+24100000000", '
                'numeroCompteur: 1, quartier: "Centre", camp: 1, indexInitial: 0, datePose: "2024-01-01"}) '
                "{ numeroAbonne } }",
                context_value=self._admin_context(),
            )
            self.assertEqual(mock_create.call_args.kwargs["position"], "")

    def test_update_abonne_success_as_admin(self) -> None:
        with (
            patch.object(auth_client, "validate_token", return_value=Mock(user_id="admin-1", role="ADMIN")),
            patch.object(abonne_client, "update_abonne", return_value=make_abonne_response(numero_abonne="AB-0002")),
        ):
            result = schema.execute_sync(
                'mutation { updateAbonne(id: "abonne-1", input: {nom: "Smith"}) { numeroAbonne } }',
                context_value=self._admin_context(),
            )

        self.assertIsNone(result.errors)
        self.assertEqual(_data(result)["updateAbonne"]["numeroAbonne"], "AB-0002")

    def test_update_abonne_telephone_invalide_rejete_sans_appel_grpc(self) -> None:
        """Champ optionnel : validé seulement s'il est fourni, mais rejeté
        avant tout appel gRPC s'il est mal formé."""
        with (
            patch.object(auth_client, "validate_token", return_value=Mock(user_id="admin-1", role="ADMIN")),
            patch.object(abonne_client, "update_abonne") as mock_update,
        ):
            result = schema.execute_sync(
                'mutation { updateAbonne(id: "abonne-1", input: {telephoneWhatsapp: "pas-un-numero"}) '
                "{ numeroAbonne } }",
                context_value=self._admin_context(),
            )

        self.assertIsNotNone(result.errors)
        self.assertIn("telephone_whatsapp invalide", str(result.errors))
        mock_update.assert_not_called()

    def test_suspendre_abonne_success_as_admin(self) -> None:
        with (
            patch.object(auth_client, "validate_token", return_value=Mock(user_id="admin-1", role="ADMIN")),
            patch.object(abonne_client, "suspendre_abonne", return_value=make_abonne_response(statut="SUSPENDU")),
        ):
            result = schema.execute_sync(
                'mutation { suspendreAbonne(id: "abonne-1") { statut } }', context_value=self._admin_context()
            )

        self.assertIsNone(result.errors)
        self.assertEqual(_data(result)["suspendreAbonne"]["statut"], "SUSPENDU")

    def test_reactiver_abonne_success_as_admin(self) -> None:
        with (
            patch.object(auth_client, "validate_token", return_value=Mock(user_id="admin-1", role="ADMIN")),
            patch.object(abonne_client, "reactiver_abonne", return_value=make_abonne_response(statut="ACTIF")),
        ):
            result = schema.execute_sync(
                'mutation { reactiverAbonne(id: "abonne-1") { statut } }', context_value=self._admin_context()
            )

        self.assertIsNone(result.errors)
        self.assertEqual(_data(result)["reactiverAbonne"]["statut"], "ACTIF")

    def test_update_compteur_success_as_admin(self) -> None:
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

    def test_update_compteur_position(self) -> None:
        with (
            patch.object(auth_client, "validate_token", return_value=Mock(user_id="admin-1", role="ADMIN")),
            patch.object(
                abonne_client, "update_compteur", return_value=make_compteur_response(position="Près du portail")
            ) as mock_update,
        ):
            result = schema.execute_sync(
                'mutation { updateCompteur(abonneId: "abonne-1", input: { position: "Près du portail" }) '
                "{ position } }",
                context_value=self._admin_context(),
            )
            mock_update.assert_called_once_with("abonne-1", position="Près du portail")

        self.assertIsNone(result.errors)
        self.assertEqual(_data(result)["updateCompteur"]["position"], "Près du portail")

    def test_update_compteur_requires_admin_role(self) -> None:
        with patch.object(auth_client, "validate_token", return_value=Mock(user_id="user-1", role="AGENT")):
            result = schema.execute_sync(
                'mutation { updateCompteur(abonneId: "abonne-1", input: { quartier: "X" }) { quartier } }',
                context_value=self._admin_context(),
            )
        self.assertIsNotNone(result.errors)
        self.assertIn("Accès non autorisé", str(result.errors))

    def test_resilier_abonne_success_as_admin(self) -> None:
        with (
            patch.object(auth_client, "validate_token", return_value=Mock(user_id="admin-1", role="ADMIN")),
            patch.object(abonne_client, "resilier_abonne", return_value=make_abonne_response(statut="RESILIE")),
        ):
            result = schema.execute_sync(
                'mutation { resilierAbonne(id: "abonne-1") { statut } }', context_value=self._admin_context()
            )

        self.assertIsNone(result.errors)
        self.assertEqual(_data(result)["resilierAbonne"]["statut"], "RESILIE")

    def test_resilier_abonne_requires_admin_role(self) -> None:
        with patch.object(auth_client, "validate_token", return_value=Mock(user_id="user-1", role="COMPTABLE")):
            result = schema.execute_sync(
                'mutation { resilierAbonne(id: "abonne-1") { statut } }', context_value=self._admin_context()
            )

        self.assertIsNotNone(result.errors)
        self.assertIn("Accès non autorisé", str(result.errors))

    def test_anonymiser_abonne_success_as_admin(self) -> None:
        with (
            patch.object(auth_client, "validate_token", return_value=Mock(user_id="admin-1", role="ADMIN")),
            patch.object(
                abonne_client,
                "anonymiser_abonne",
                return_value=make_abonne_response(statut="RESILIE", nom="Abonné anonymisé"),
            ) as mock_anonymiser,
        ):
            result = schema.execute_sync(
                'mutation { anonymiserAbonne(abonneId: "abonne-1") { statut nom } }',
                context_value=self._admin_context(),
            )
            mock_anonymiser.assert_called_once_with("abonne-1")

        self.assertIsNone(result.errors)
        self.assertEqual(_data(result)["anonymiserAbonne"]["statut"], "RESILIE")
        self.assertEqual(_data(result)["anonymiserAbonne"]["nom"], "Abonné anonymisé")

    def test_anonymiser_abonne_requires_admin_role(self) -> None:
        with patch.object(auth_client, "validate_token", return_value=Mock(user_id="user-1", role="COMPTABLE")):
            result = schema.execute_sync(
                'mutation { anonymiserAbonne(abonneId: "abonne-1") { statut } }',
                context_value=self._admin_context(),
            )

        self.assertIsNotNone(result.errors)
        self.assertIn("Accès non autorisé", str(result.errors))

    def test_exporter_donnees_abonne_success_as_admin(self) -> None:
        with (
            patch.object(auth_client, "validate_token", return_value=Mock(user_id="admin-1", role="ADMIN")),
            patch.object(
                abonne_client,
                "exporter_donnees_abonne",
                return_value=Mock(json_export='{"abonne_id": "abonne-1"}'),
            ) as mock_export,
        ):
            result = schema.execute_sync(
                'mutation { exporterDonneesAbonne(abonneId: "abonne-1") }',
                context_value=self._admin_context(),
            )
            mock_export.assert_called_once_with("abonne-1")

        self.assertIsNone(result.errors)
        self.assertEqual(_data(result)["exporterDonneesAbonne"], '{"abonne_id": "abonne-1"}')

    def test_exporter_donnees_abonne_requires_admin_role(self) -> None:
        with patch.object(auth_client, "validate_token", return_value=Mock(user_id="user-1", role="AGENT")):
            result = schema.execute_sync(
                'mutation { exporterDonneesAbonne(abonneId: "abonne-1") }',
                context_value=self._admin_context(),
            )

        self.assertIsNotNone(result.errors)
        self.assertIn("Accès non autorisé", str(result.errors))

    def test_remplacer_compteur_requires_admin_role(self) -> None:
        with patch.object(auth_client, "validate_token", return_value=Mock(user_id="user-1", role="COMPTABLE")):
            result = schema.execute_sync(
                'mutation { remplacerCompteur(abonneId: "abonne-1", input: {indexFermeture: 100, '
                'nouveauNumeroCompteur: 2, nouveauQuartier: "Q", nouveauCamp: 2, nouvelIndexInitial: 0, '
                'dateRemplacement: "2024-06-01"}) { numeroCompteur } }',
                context_value=self._admin_context(),
            )

        self.assertIsNotNone(result.errors)
        self.assertIn("Accès non autorisé", str(result.errors))

    def test_remplacer_compteur_success_as_admin(self) -> None:
        with (
            patch.object(auth_client, "validate_token", return_value=Mock(user_id="admin-1", role="ADMIN")),
            patch.object(
                abonne_client, "remplacer_compteur", return_value=make_compteur_response(numero_compteur=2)
            ) as mock_remplacer,
        ):
            result = schema.execute_sync(
                'mutation { remplacerCompteur(abonneId: "abonne-1", input: {indexFermeture: 100, '
                'nouveauNumeroCompteur: 2, nouveauQuartier: "Q", nouveauCamp: 2, nouvelIndexInitial: 0, '
                'dateRemplacement: "2024-06-01", motif: "Compteur défectueux"}) { numeroCompteur } }',
                context_value=self._admin_context(),
            )
            # Le motif saisi est bien propagé jusqu'au client gRPC.
            self.assertEqual(mock_remplacer.call_args.kwargs["motif"], "Compteur défectueux")

        self.assertIsNone(result.errors)
        self.assertEqual(_data(result)["remplacerCompteur"]["numeroCompteur"], 2)

    def test_remplacer_compteur_transporte_la_nouvelle_position(self) -> None:
        with (
            patch.object(auth_client, "validate_token", return_value=Mock(user_id="admin-1", role="ADMIN")),
            patch.object(
                abonne_client,
                "remplacer_compteur",
                return_value=make_compteur_response(numero_compteur=2, position="Près du portail bleu"),
            ) as mock_remplacer,
        ):
            result = schema.execute_sync(
                'mutation { remplacerCompteur(abonneId: "abonne-1", input: {indexFermeture: 100, '
                'nouveauNumeroCompteur: 2, nouveauQuartier: "Q", nouveauCamp: 2, nouvelIndexInitial: 0, '
                'dateRemplacement: "2024-06-01", nouvellePosition: "Près du portail bleu"}) { position } }',
                context_value=self._admin_context(),
            )
            self.assertEqual(mock_remplacer.call_args.kwargs["nouvelle_position"], "Près du portail bleu")

        self.assertIsNone(result.errors)
        self.assertEqual(_data(result)["remplacerCompteur"]["position"], "Près du portail bleu")


class AbonnesActifsQueryTests(SimpleTestCase):
    def test_abonnes_actifs_returns_list(self) -> None:
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
        self.assertEqual(len(_data(result)["abonnesActifs"]), 2)
        self.assertEqual(_data(result)["abonnesActifs"][0]["statut"], "ACTIF")


class HistoriqueCompteurQueryTests(SimpleTestCase):
    def _make_historique_response(self) -> Mock:
        h = Mock()
        h.historique_id = "histo-1"
        h.ancien_compteur = make_compteur_response(numero_compteur=1, statut="REMPLACE")
        h.nouveau_compteur = make_compteur_response(numero_compteur=2, statut="ACTIF")
        h.index_fermeture = 120.0
        h.date_remplacement = "2024-06-01"
        h.created_at = "2024-06-01T08:00:00"
        h.motif = "Compteur défectueux"
        return h

    def test_historique_compteur_returns_list(self) -> None:
        with (
            patch.object(auth_client, "validate_token", return_value=Mock(user_id="admin-1", role="ADMIN")),
            patch.object(
                abonne_client,
                "get_historique_compteur",
                return_value=Mock(historique=[self._make_historique_response()]),
            ),
        ):
            result = schema.execute_sync(
                'query { historiqueCompteur(id: "abonne-1") { id indexFermeture motif '
                "ancienCompteur { numeroCompteur statut } nouveauCompteur { numeroCompteur } } }",
                context_value=context(token="access-1"),
            )

        self.assertIsNone(result.errors)
        self.assertEqual(len(_data(result)["historiqueCompteur"]), 1)
        entry = _data(result)["historiqueCompteur"][0]
        self.assertEqual(entry["indexFermeture"], 120.0)
        self.assertEqual(entry["motif"], "Compteur défectueux")
        self.assertEqual(entry["ancienCompteur"]["numeroCompteur"], 1)
        self.assertEqual(entry["ancienCompteur"]["statut"], "REMPLACE")
        self.assertEqual(entry["nouveauCompteur"]["numeroCompteur"], 2)

    def test_historique_compteur_empty_returns_empty_list(self) -> None:
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
        self.assertEqual(_data(result)["historiqueCompteur"], [])
