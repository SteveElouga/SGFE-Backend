"""Tests des resolvers GraphQL du Reporting Service (gateway)."""

from unittest.mock import MagicMock, patch

from django.test import SimpleTestCase

from schema.reporting_queries import ReportingQueries


def _stats_campagne(campagne_id="c1"):
    return MagicMock(
        campagne_id=campagne_id,
        nom_campagne="Juin 2026",
        total_abonnes=50,
        nb_releves=40,
        nb_en_attente=10,
        pourcentage_progression=80.0,
        consommation_totale=1200.0,
    )


def _stats_facturation(campagne_id="c1"):
    return MagicMock(
        campagne_id=campagne_id,
        total_factures=42,
        montant_total_facture=210000.0,
        nb_factures_envoyees=40,
        nb_factures_payees=3,
        nb_impayes=39,
    )


def _stats_paiements(campagne_id="c1"):
    return MagicMock(
        campagne_id=campagne_id,
        montant_encaisse=52500.0,
        montant_impaye=157500.0,
        nb_impayes=39,
        taux_recouvrement=25.0,
    )


class DashboardQueryTests(SimpleTestCase):
    @patch("schema.reporting_queries.reporting_client")
    @patch("schema.reporting_queries.require_role")
    def test_dashboard_avec_donnees(self, mock_role, mock_client):
        mock_client.get_dashboard.return_value = MagicMock(
            campagne_en_cours=_stats_campagne(),
            facturation_en_cours=_stats_facturation(),
            paiements_en_cours=_stats_paiements(),
        )
        result = ReportingQueries().dashboard(MagicMock())
        self.assertEqual(result.campagne_en_cours.nom_campagne, "Juin 2026")
        self.assertEqual(result.facturation_en_cours.total_factures, 42)
        self.assertAlmostEqual(result.paiements_en_cours.taux_recouvrement, 25.0)

    @patch("schema.reporting_queries.reporting_client")
    @patch("schema.reporting_queries.require_role")
    def test_dashboard_vide_sous_blocs_nuls(self, mock_role, mock_client):
        vide_c = MagicMock(campagne_id="")
        vide_f = MagicMock(campagne_id="")
        vide_p = MagicMock(campagne_id="")
        mock_client.get_dashboard.return_value = MagicMock(
            campagne_en_cours=vide_c, facturation_en_cours=vide_f, paiements_en_cours=vide_p
        )
        result = ReportingQueries().dashboard(MagicMock())
        self.assertIsNone(result.campagne_en_cours)
        self.assertIsNone(result.facturation_en_cours)
        self.assertIsNone(result.paiements_en_cours)

    @patch("schema.reporting_queries.reporting_client")
    @patch("schema.reporting_queries.require_role")
    def test_dashboard_gate_admin_comptable(self, mock_role, mock_client):
        mock_client.get_dashboard.return_value = MagicMock(
            campagne_en_cours=MagicMock(campagne_id=""),
            facturation_en_cours=MagicMock(campagne_id=""),
            paiements_en_cours=MagicMock(campagne_id=""),
        )
        info = MagicMock()
        ReportingQueries().dashboard(info)
        mock_role.assert_called_once_with(info, "ADMIN", "COMPTABLE")

    @patch("schema.reporting_queries.reporting_client")
    @patch("schema.reporting_queries.require_role")
    def test_stats_campagne(self, mock_role, mock_client):
        mock_client.get_stats_campagne.return_value = _stats_campagne()
        result = ReportingQueries().stats_campagne(MagicMock(), campagne_id="c1")
        self.assertEqual(result.total_abonnes, 50)
        mock_client.get_stats_campagne.assert_called_once_with("c1")

    @patch("schema.reporting_queries.reporting_client")
    @patch("schema.reporting_queries.require_role")
    def test_stats_globales(self, mock_role, mock_client):
        mock_client.get_stats_globales.return_value = MagicMock(
            historique_campagnes=[_stats_campagne("c1"), _stats_campagne("c2")],
            consommation_totale_globale=3000.0,
            montant_total_facture_global=500000.0,
            montant_total_encaisse_global=200000.0,
        )
        result = ReportingQueries().stats_globales(MagicMock())
        self.assertEqual(len(result.historique_campagnes), 2)
        self.assertAlmostEqual(result.montant_total_encaisse_global, 200000.0)
