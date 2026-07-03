"""Tests des resolvers GraphQL du Paiement Service (gateway).

Régression ANO-022 : aucun test n'existait pour ce domaine.
"""

from unittest.mock import MagicMock, patch

from django.test import SimpleTestCase

from schema.paiement_mutations import PaiementMutations
from schema.paiement_queries import PaiementQueries


def _solde_response(**kwargs) -> MagicMock:
    defaults = dict(
        facture_id="facture-001", montant_total=25000.0, montant_paye=10000.0, solde_restant=15000.0, statut="PARTIELLE"
    )
    defaults.update(kwargs)
    return MagicMock(**defaults)


def _paiement_response(**kwargs) -> MagicMock:
    defaults = dict(
        paiement_id="paiement-001",
        facture_id="facture-001",
        montant=10000.0,
        date_paiement="2026-07-02",
        mode_paiement="MOBILE_MONEY",
        reference_transaction="TXN123",
        created_at="2026-07-02T10:00:00",
    )
    defaults.update(kwargs)
    return MagicMock(**defaults)


def _suivi_response(**kwargs) -> MagicMock:
    defaults = dict(
        suivi_id="suivi-001",
        facture_id="facture-001",
        abonne_id="abonne-001",
        date_depassement="2026-07-06",
        etape_actuelle=1,
        resolu_le="",
    )
    defaults.update(kwargs)
    return MagicMock(**defaults)


class TestPaiementQueries(SimpleTestCase):
    @patch("schema.paiement_queries.paiement_client")
    @patch("schema.paiement_queries.require_auth")
    @patch("schema.paiement_queries.require_role")
    def test_solde_facture(self, mock_role, mock_auth, mock_client) -> None:
        mock_auth.return_value = MagicMock(role="COMPTABLE")
        mock_client.get_solde.return_value = _solde_response()
        info = MagicMock()
        result = PaiementQueries().solde_facture(info, facture_id="facture-001")
        self.assertEqual(result.solde_restant, 15000.0)
        self.assertEqual(result.statut, "PARTIELLE")

    @patch("schema.paiement_queries.paiement_client")
    @patch("schema.paiement_queries.require_auth")
    @patch("schema.paiement_queries.require_role")
    def test_paiements_avec_filtre(self, mock_role, mock_auth, mock_client) -> None:
        mock_auth.return_value = MagicMock(role="ADMIN")
        mock_client.list_paiements.return_value = MagicMock(paiements=[_paiement_response()])
        info = MagicMock()
        result = PaiementQueries().paiements(info, facture_id="facture-001")
        self.assertEqual(len(result), 1)
        self.assertEqual(result[0].montant, 10000.0)

    @patch("schema.paiement_queries.paiement_client")
    @patch("schema.paiement_queries.require_auth")
    @patch("schema.paiement_queries.require_role")
    def test_impayes(self, mock_role, mock_auth, mock_client) -> None:
        mock_auth.return_value = MagicMock(role="ADMIN")
        mock_client.list_impayes.return_value = MagicMock(impayes=[_solde_response(statut="IMPAYEE")])
        info = MagicMock()
        result = PaiementQueries().impayes(info)
        self.assertEqual(len(result), 1)
        self.assertEqual(result[0].statut, "IMPAYEE")

    @patch("schema.paiement_queries.paiement_client")
    @patch("schema.paiement_queries.require_auth")
    @patch("schema.paiement_queries.require_role")
    def test_suivi_impaye(self, mock_role, mock_auth, mock_client) -> None:
        mock_auth.return_value = MagicMock(role="ADMIN")
        mock_client.get_suivi_impaye.return_value = _suivi_response(etape_actuelle=2)
        info = MagicMock()
        result = PaiementQueries().suivi_impaye(info, facture_id="facture-001")
        self.assertEqual(result.etape_actuelle, 2)


class TestPaiementMutations(SimpleTestCase):
    @patch("schema.paiement_mutations.paiement_client")
    @patch("schema.paiement_mutations.require_role")
    def test_enregistrer_paiement(self, mock_role, mock_client) -> None:
        mock_role.return_value = MagicMock(role="COMPTABLE", user_id="user-001")
        mock_client.enregistrer_paiement.return_value = _paiement_response()
        info = MagicMock()
        result = PaiementMutations().enregistrer_paiement(
            info,
            facture_id="facture-001",
            abonne_id="abonne-001",
            montant=10000.0,
            date_paiement="2026-07-02",
            mode_paiement="MOBILE_MONEY",
            reference_transaction="TXN123",
        )
        self.assertEqual(result.montant, 10000.0)
        mock_client.enregistrer_paiement.assert_called_once_with(
            facture_id="facture-001",
            abonne_id="abonne-001",
            montant=10000.0,
            date_paiement="2026-07-02",
            mode_paiement="MOBILE_MONEY",
            reference_transaction="TXN123",
            enregistre_par="user-001",
        )

    @patch("schema.paiement_mutations.paiement_client")
    @patch("schema.paiement_mutations.require_role")
    def test_enregistrer_paiement_reference_optionnelle(self, mock_role, mock_client) -> None:
        """Le paiement ESPECES n'exige pas de reference_transaction (défaut '')."""
        mock_role.return_value = MagicMock(role="ADMIN", user_id="user-002")
        mock_client.enregistrer_paiement.return_value = _paiement_response(
            mode_paiement="ESPECES", reference_transaction=""
        )
        info = MagicMock()
        result = PaiementMutations().enregistrer_paiement(
            info,
            facture_id="facture-001",
            abonne_id="abonne-001",
            montant=25000.0,
            date_paiement="2026-07-02",
            mode_paiement="ESPECES",
        )
        self.assertEqual(result.mode_paiement, "ESPECES")
