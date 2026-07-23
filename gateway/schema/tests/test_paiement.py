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
        enregistre_par="user-001",
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


def _avoir_response(**kwargs) -> MagicMock:
    mouvement = MagicMock(
        montant=100.0,
        type_mouvement="RECTIFICATION",
        motif="Geste commercial",
        facture_id="",
        cree_par="user-001",
        created_at="2026-07-02T10:00:00",
    )
    defaults = dict(abonne_id="abonne-001", montant=100.0, mouvements=[mouvement])
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

    @patch("schema.paiement_queries.auth_client")
    @patch("schema.paiement_queries.paiement_client")
    @patch("schema.paiement_queries.require_auth")
    @patch("schema.paiement_queries.require_role")
    def test_paiements_avec_filtre(self, mock_role, mock_auth, mock_client, mock_auth_client) -> None:
        mock_auth.return_value = MagicMock(role="ADMIN")
        mock_client.list_paiements.return_value = MagicMock(paiements=[_paiement_response()])
        mock_auth_client.get_user.return_value = MagicMock(username="bah.comptable")
        info = MagicMock()
        result = PaiementQueries().paiements(info, facture_id="facture-001")
        self.assertEqual(len(result), 1)
        self.assertEqual(result[0].montant, 10000.0)
        self.assertEqual(result[0].operateur, "bah.comptable")
        mock_auth_client.get_user.assert_called_once_with("user-001")

    @patch("schema.paiement_queries.auth_client")
    @patch("schema.paiement_queries.paiement_client")
    @patch("schema.paiement_queries.require_auth")
    @patch("schema.paiement_queries.require_role")
    def test_paiements_operateur_non_resolu_replie_sur_identifiant(
        self, mock_role, mock_auth, mock_client, mock_auth_client
    ) -> None:
        """Auth Service indisponible : la liste des paiements reste servie (dégradation gracieuse)."""
        mock_auth.return_value = MagicMock(role="ADMIN")
        mock_client.list_paiements.return_value = MagicMock(paiements=[_paiement_response(enregistre_par="user-999")])
        mock_auth_client.get_user.side_effect = RuntimeError("Auth Service indisponible")
        info = MagicMock()
        result = PaiementQueries().paiements(info, facture_id="facture-001")
        self.assertEqual(result[0].operateur, "Utilisateur user-999")

    @patch("schema.paiement_queries.auth_client")
    @patch("schema.paiement_queries.paiement_client")
    @patch("schema.paiement_queries.require_auth")
    @patch("schema.paiement_queries.require_role")
    def test_paiements_resout_chaque_operateur_une_seule_fois(
        self, mock_role, mock_auth, mock_client, mock_auth_client
    ) -> None:
        """Plusieurs paiements du même opérateur ne déclenchent qu'un seul appel gRPC."""
        mock_auth.return_value = MagicMock(role="ADMIN")
        mock_client.list_paiements.return_value = MagicMock(
            paiements=[
                _paiement_response(paiement_id="p1", enregistre_par="user-001"),
                _paiement_response(paiement_id="p2", enregistre_par="user-001"),
            ]
        )
        mock_auth_client.get_user.return_value = MagicMock(username="bah.comptable")
        info = MagicMock()
        result = PaiementQueries().paiements(info)
        self.assertEqual(len(result), 2)
        self.assertEqual(mock_auth_client.get_user.call_count, 1)

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

    @patch("schema.paiement_queries.paiement_client")
    @patch("schema.paiement_queries.require_auth")
    @patch("schema.paiement_queries.require_role")
    def test_avoir_abonne(self, mock_role, mock_auth, mock_client) -> None:
        mock_auth.return_value = MagicMock(role="COMPTABLE")
        mock_client.get_avoir_abonne.return_value = _avoir_response(montant=100.0)
        info = MagicMock()
        result = PaiementQueries().avoir_abonne(info, abonne_id="abonne-001")
        self.assertEqual(result.montant, 100.0)
        self.assertEqual(len(result.mouvements), 1)
        self.assertEqual(result.mouvements[0].type_mouvement, "RECTIFICATION")


class TestPaiementMutations(SimpleTestCase):
    @patch("schema.paiement_mutations.paiement_client")
    @patch("schema.paiement_mutations.require_role")
    def test_enregistrer_paiement(self, mock_role, mock_client) -> None:
        mock_role.return_value = MagicMock(role="COMPTABLE", user_id="user-001", username="bah.comptable")
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
        # L'opérateur affiché est le nom d'utilisateur courant, déjà connu du
        # payload JWT — aucun aller-retour vers Auth Service pour la mutation.
        self.assertEqual(result.operateur, "bah.comptable")
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
    def test_crediter_avoir(self, mock_role, mock_client) -> None:
        mock_role.return_value = MagicMock(role="ADMIN", user_id="user-001", username="admin")
        mock_client.crediter_avoir.return_value = _avoir_response(montant=250.0)
        info = MagicMock()
        result = PaiementMutations().crediter_avoir(
            info, abonne_id="abonne-001", montant=250.0, motif="Erreur d'index corrigée"
        )
        self.assertEqual(result.montant, 250.0)
        mock_client.crediter_avoir.assert_called_once_with(
            abonne_id="abonne-001",
            montant=250.0,
            motif="Erreur d'index corrigée",
            cree_par="user-001",
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
