"""Tests des resolvers GraphQL du Facturation Service (gateway).

Régression ANO-022 : aucun test n'existait pour ce domaine.
"""

from unittest.mock import MagicMock, patch

from django.test import SimpleTestCase

from schema.facturation_mutations import FacturationMutations
from schema.facturation_queries import FacturationQueries


def _facture_response(**kwargs) -> MagicMock:
    defaults = dict(
        facture_id="facture-001",
        numero_facture="FACT-2026-07-0001",
        abonne_id="abonne-001",
        campagne_id="camp-001",
        ancien_index=100.0,
        nouveau_index=150.0,
        consommation=50.0,
        prix_m3=500.0,
        montant=25000.0,
        statut="IMPAYEE",
        date_releve="2026-07-01",
        date_limite_paiement="2026-07-06",
        date_generation="2026-07-01T10:00:00",
        pdf_path="/pdfs/FACT-2026-07-0001.pdf",
        numero_mobile_money="",
    )
    defaults.update(kwargs)
    return MagicMock(**defaults)


def _tarif_response(**kwargs) -> MagicMock:
    defaults = dict(tarif_id="tarif-001", prix_m3=500.0, date_effet="2026-01-01", is_active=True)
    defaults.update(kwargs)
    return MagicMock(**defaults)


class TestFacturationQueries(SimpleTestCase):
    @patch("schema.facturation_queries.facturation_client")
    @patch("schema.facturation_queries.require_auth")
    @patch("schema.facturation_queries.require_role")
    def test_tarif_actuel_admin(self, mock_role, mock_auth, mock_client) -> None:
        mock_auth.return_value = MagicMock(role="ADMIN")
        mock_client.get_tarif_actuel.return_value = _tarif_response()
        info = MagicMock()
        result = FacturationQueries().tarif_actuel(info)
        self.assertEqual(result.prix_m3, 500.0)
        self.assertTrue(result.is_active)

    @patch("schema.facturation_queries.facturation_client")
    @patch("schema.facturation_queries.require_auth")
    @patch("schema.facturation_queries.require_role")
    def test_facture_par_id(self, mock_role, mock_auth, mock_client) -> None:
        mock_auth.return_value = MagicMock(role="COMPTABLE")
        mock_client.get_facture.return_value = _facture_response()
        info = MagicMock()
        result = FacturationQueries().facture(info, facture_id="facture-001")
        self.assertEqual(result.facture_id, "facture-001")
        self.assertEqual(result.montant, 25000.0)

    @patch("schema.facturation_queries.facturation_client")
    @patch("schema.facturation_queries.require_auth")
    @patch("schema.facturation_queries.require_role")
    def test_factures_avec_filtres(self, mock_role, mock_auth, mock_client) -> None:
        mock_auth.return_value = MagicMock(role="ADMIN")
        mock_client.list_factures.return_value = MagicMock(
            factures=[_facture_response(), _facture_response(facture_id="facture-002")]
        )
        info = MagicMock()
        result = FacturationQueries().factures(info, campagne_id="camp-001", statut="IMPAYEE")
        self.assertEqual(len(result), 2)
        mock_client.list_factures.assert_called_once_with(campagne_id="camp-001", abonne_id="", statut="IMPAYEE")

    @patch("schema.facturation_queries.facturation_client")
    @patch("schema.facturation_queries.require_auth")
    @patch("schema.facturation_queries.require_role")
    def test_factures_par_campagne(self, mock_role, mock_auth, mock_client) -> None:
        mock_auth.return_value = MagicMock(role="ADMIN")
        mock_client.get_factures_par_campagne.return_value = MagicMock(factures=[_facture_response()])
        info = MagicMock()
        result = FacturationQueries().factures_par_campagne(info, campagne_id="camp-001")
        self.assertEqual(len(result), 1)


class TestFacturationMutations(SimpleTestCase):
    @patch("schema.facturation_mutations.facturation_client")
    @patch("schema.facturation_mutations.require_auth")
    @patch("schema.facturation_mutations.require_role")
    def test_update_tarif_admin(self, mock_role, mock_auth, mock_client) -> None:
        mock_auth.return_value = MagicMock(role="ADMIN")
        mock_client.update_tarif.return_value = _tarif_response(prix_m3=600.0)
        info = MagicMock()
        result = FacturationMutations().update_tarif(info, prix_m3=600.0, date_effet="2026-08-01")
        self.assertEqual(result.prix_m3, 600.0)
        mock_client.update_tarif.assert_called_once_with(prix_m3=600.0, date_effet="2026-08-01")

    @patch("schema.facturation_mutations.facturation_client")
    @patch("schema.facturation_mutations.require_auth")
    @patch("schema.facturation_mutations.require_role")
    def test_generer_factures(self, mock_role, mock_auth, mock_client) -> None:
        mock_auth.return_value = MagicMock(role="COMPTABLE")
        mock_client.generer_factures.return_value = MagicMock(factures=[_facture_response()])
        info = MagicMock()
        result = FacturationMutations().generer_factures(info, campagne_id="camp-001")
        self.assertEqual(len(result), 1)

    @patch("schema.facturation_mutations.notification_client")
    @patch("schema.facturation_mutations.facturation_client")
    @patch("schema.facturation_mutations.require_auth")
    @patch("schema.facturation_mutations.require_role")
    def test_envoyer_toutes_factures_whatsapp_compte_les_succes(
        self, mock_role, mock_auth, mock_fact_client, mock_notif_client
    ) -> None:
        import grpc

        mock_auth.return_value = MagicMock(role="ADMIN")
        mock_fact_client.get_factures_par_campagne.return_value = MagicMock(
            factures=[_facture_response(facture_id="f1"), _facture_response(facture_id="f2")]
        )
        # f1 réussit, f2 échoue — dégradation gracieuse, ne doit pas interrompre le lot
        mock_notif_client.renvoyer_facture.side_effect = [None, grpc.RpcError("échec")]
        info = MagicMock()
        result = FacturationMutations().envoyer_toutes_factures_whatsapp(info, campagne_id="camp-001")
        self.assertEqual(result, 1)

    @patch("schema.facturation_mutations.facturation_client")
    @patch("schema.facturation_mutations.require_auth")
    @patch("schema.facturation_mutations.require_role")
    def test_update_statut_facture(self, mock_role, mock_auth, mock_client) -> None:
        mock_auth.return_value = MagicMock(role="COMPTABLE")
        mock_client.update_statut_facture.return_value = _facture_response(statut="PAYEE")
        info = MagicMock()
        result = FacturationMutations().update_statut_facture(info, facture_id="facture-001", statut="PAYEE")
        self.assertEqual(result.statut, "PAYEE")
