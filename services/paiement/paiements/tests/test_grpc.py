"""Tests du serveur gRPC du Paiement Service."""

import sys
from datetime import date, timedelta
from decimal import Decimal
from pathlib import Path
from unittest.mock import MagicMock, patch

import grpc
from django.conf import settings
from django.test import TestCase

sys.path.insert(0, str(Path(settings.BASE_DIR) / "proto"))

import paiement_service_pb2 as pb

from paiements.grpc_server import PaiementServicer
from paiements.models import ModePaiement, SoldeFacture, StatutSolde
from paiements.repositories import SoldeFactureRepository
from paiements.services import PaiementService


def _mock_context() -> MagicMock:
    """Crée un contexte gRPC mocké qui lève une exception sur abort."""
    ctx = MagicMock(spec=grpc.ServicerContext)
    ctx.abort.side_effect = Exception("aborted")
    return ctx


def _creer_solde(
    facture_id: str = "facture-001",
    abonne_id: str = "abonne-001",
    montant: float = 300.00,
    date_limite: date | None = None,
) -> SoldeFacture:
    """Crée un SoldeFacture de test."""
    return SoldeFactureRepository().create(
        facture_id=facture_id,
        abonne_id=abonne_id,
        montant_total=Decimal(str(montant)),
        date_limite_paiement=date_limite or date(2026, 7, 31),
    )


class TestInitialiserSoldeRPC(TestCase):
    """Tests du RPC InitialiserSolde."""

    def setUp(self) -> None:
        with patch("paiements.grpc_server.FacturationServiceClient"):
            self.servicer = PaiementServicer()

    def test_initialiser_solde_succes(self) -> None:
        """InitialiserSolde crée un SoldeFacture et retourne SoldeResponse."""
        request = pb.InitialiserSoldeRequest(
            facture_id="facture-001",
            abonne_id="abonne-001",
            montant_total=300.00,
            date_limite_paiement="2026-07-31",
        )
        response = self.servicer.InitialiserSolde(request, _mock_context())
        self.assertEqual(response.facture_id, "facture-001")
        self.assertEqual(response.statut, StatutSolde.IMPAYEE)
        self.assertAlmostEqual(response.montant_total, 300.00)
        self.assertAlmostEqual(response.montant_paye, 0.0)

    def test_initialiser_solde_montant_nul_abort(self) -> None:
        """Un montant nul provoque un abort INVALID_ARGUMENT."""
        request = pb.InitialiserSoldeRequest(
            facture_id="facture-002",
            abonne_id="abonne-001",
            montant_total=0.0,
            date_limite_paiement="2026-07-31",
        )
        ctx = _mock_context()
        with self.assertRaises(Exception):
            self.servicer.InitialiserSolde(request, ctx)
        ctx.abort.assert_called_once()
        self.assertEqual(ctx.abort.call_args[0][0], grpc.StatusCode.INVALID_ARGUMENT)

    def test_initialiser_solde_date_invalide_abort(self) -> None:
        """Une date mal formatée provoque un abort INVALID_ARGUMENT."""
        request = pb.InitialiserSoldeRequest(
            facture_id="facture-003",
            abonne_id="abonne-001",
            montant_total=100.00,
            date_limite_paiement="pas-une-date",
        )
        ctx = _mock_context()
        with self.assertRaises(Exception):
            self.servicer.InitialiserSolde(request, ctx)
        ctx.abort.assert_called_once()


class TestEnregistrerPaiementRPC(TestCase):
    """Tests du RPC EnregistrerPaiement."""

    def setUp(self) -> None:
        with patch("paiements.grpc_server.FacturationServiceClient"):
            self.servicer = PaiementServicer()
        _creer_solde("facture-001", "abonne-001", 300.00)

    @patch("paiements.grpc_server.FacturationServiceClient")
    def test_enregistrer_paiement_succes(self, mock_fact_cls) -> None:
        """EnregistrerPaiement retourne un PaiementResponse valide."""
        mock_fact_cls.return_value.update_statut_facture = MagicMock()
        with patch("paiements.grpc_server.FacturationServiceClient"):
            servicer = PaiementServicer()
        request = pb.EnregistrerPaiementRequest(
            facture_id="facture-001",
            abonne_id="abonne-001",
            montant=100.00,
            date_paiement="2026-06-20",
            mode_paiement="ESPECES",
            reference_transaction="",
            enregistre_par="user-001",
        )
        response = servicer.EnregistrerPaiement(request, _mock_context())
        self.assertIsNotNone(response.paiement_id)
        self.assertEqual(response.facture_id, "facture-001")
        self.assertAlmostEqual(response.montant, 100.00)
        self.assertEqual(response.enregistre_par, "user-001")

    def test_enregistrer_paiement_montant_invalide_abort(self) -> None:
        """Un montant nul provoque un abort INVALID_ARGUMENT."""
        request = pb.EnregistrerPaiementRequest(
            facture_id="facture-001",
            abonne_id="abonne-001",
            montant=0.0,
            date_paiement="2026-06-20",
            mode_paiement="ESPECES",
            reference_transaction="",
            enregistre_par="user-001",
        )
        ctx = _mock_context()
        with self.assertRaises(Exception):
            self.servicer.EnregistrerPaiement(request, ctx)
        ctx.abort.assert_called_once()
        self.assertEqual(ctx.abort.call_args[0][0], grpc.StatusCode.INVALID_ARGUMENT)

    def test_enregistrer_paiement_surpaiement_abort(self) -> None:
        """Un surpaiement provoque un abort INVALID_ARGUMENT."""
        request = pb.EnregistrerPaiementRequest(
            facture_id="facture-001",
            abonne_id="abonne-001",
            montant=500.00,
            date_paiement="2026-06-20",
            mode_paiement="ESPECES",
            reference_transaction="",
            enregistre_par="user-001",
        )
        ctx = _mock_context()
        with self.assertRaises(Exception):
            self.servicer.EnregistrerPaiement(request, ctx)
        ctx.abort.assert_called_once()
        self.assertEqual(ctx.abort.call_args[0][0], grpc.StatusCode.INVALID_ARGUMENT)

    def test_enregistrer_paiement_facture_inconnue_abort(self) -> None:
        """Paiement sur facture inconnue provoque un abort NOT_FOUND."""
        request = pb.EnregistrerPaiementRequest(
            facture_id="facture-inconnue",
            abonne_id="abonne-001",
            montant=100.00,
            date_paiement="2026-06-20",
            mode_paiement="ESPECES",
            reference_transaction="",
            enregistre_par="user-001",
        )
        ctx = _mock_context()
        with self.assertRaises(Exception):
            self.servicer.EnregistrerPaiement(request, ctx)
        ctx.abort.assert_called_once()
        self.assertEqual(ctx.abort.call_args[0][0], grpc.StatusCode.NOT_FOUND)

    def test_enregistrer_paiement_mobile_money_sans_reference_abort(self) -> None:
        """MOBILE_MONEY sans référence provoque un abort INVALID_ARGUMENT."""
        request = pb.EnregistrerPaiementRequest(
            facture_id="facture-001",
            abonne_id="abonne-001",
            montant=100.00,
            date_paiement="2026-06-20",
            mode_paiement="MOBILE_MONEY",
            reference_transaction="",
            enregistre_par="user-001",
        )
        ctx = _mock_context()
        with self.assertRaises(Exception):
            self.servicer.EnregistrerPaiement(request, ctx)
        ctx.abort.assert_called_once()
        self.assertEqual(ctx.abort.call_args[0][0], grpc.StatusCode.INVALID_ARGUMENT)


class TestGetSoldeRPC(TestCase):
    """Tests du RPC GetSolde."""

    def setUp(self) -> None:
        with patch("paiements.grpc_server.FacturationServiceClient"):
            self.servicer = PaiementServicer()

    def test_get_solde_succes(self) -> None:
        """GetSolde retourne le solde d'une facture existante."""
        _creer_solde("facture-001")
        request = pb.FactureIdRequest(facture_id="facture-001")
        response = self.servicer.GetSolde(request, _mock_context())
        self.assertEqual(response.facture_id, "facture-001")
        self.assertEqual(response.statut, StatutSolde.IMPAYEE)

    def test_get_solde_facture_inconnue_abort(self) -> None:
        """GetSolde avec facture inconnue provoque un abort NOT_FOUND."""
        request = pb.FactureIdRequest(facture_id="facture-inconnue")
        ctx = _mock_context()
        with self.assertRaises(Exception):
            self.servicer.GetSolde(request, ctx)
        ctx.abort.assert_called_once()
        self.assertEqual(ctx.abort.call_args[0][0], grpc.StatusCode.NOT_FOUND)


class TestListPaiementsRPC(TestCase):
    """Tests du RPC ListPaiements."""

    def setUp(self) -> None:
        with patch("paiements.grpc_server.FacturationServiceClient"):
            self.servicer = PaiementServicer()
        _creer_solde("facture-001", "abonne-001", 500.00)

    def test_list_paiements_retourne_liste(self) -> None:
        """ListPaiements retourne les paiements de la facture."""
        # Enregistrer un paiement
        svc = PaiementService()
        svc.enregistrer_paiement(
            "facture-001",
            "abonne-001",
            100.0,
            date.today(),
            ModePaiement.ESPECES,
            "",
            "user-001",
        )
        request = pb.ListPaiementsRequest(facture_id="facture-001", abonne_id="")
        response = self.servicer.ListPaiements(request, _mock_context())
        self.assertEqual(len(response.paiements), 1)
        self.assertEqual(response.paiements[0].facture_id, "facture-001")
        self.assertEqual(response.paiements[0].enregistre_par, "user-001")

    def test_list_paiements_vide_retourne_liste_vide(self) -> None:
        """ListPaiements retourne une liste vide si aucun paiement."""
        request = pb.ListPaiementsRequest(facture_id="facture-001", abonne_id="")
        response = self.servicer.ListPaiements(request, _mock_context())
        self.assertEqual(len(response.paiements), 0)


class TestListImpayesRPC(TestCase):
    """Tests du RPC ListImpayes."""

    def setUp(self) -> None:
        with patch("paiements.grpc_server.FacturationServiceClient"):
            self.servicer = PaiementServicer()

    def test_list_impayes_retourne_la_liste(self) -> None:
        """ListImpayes retourne les soldes en retard non payés."""
        _creer_solde("facture-retard", date_limite=date.today() - timedelta(days=3))
        request = pb.EmptyRequest()
        response = self.servicer.ListImpayes(request, _mock_context())
        self.assertEqual(len(response.impayes), 1)
        self.assertEqual(response.impayes[0].facture_id, "facture-retard")

    def test_list_impayes_vide_si_aucun_retard(self) -> None:
        """ListImpayes retourne une liste vide si toutes les factures sont dans les délais."""
        _creer_solde("facture-ok", date_limite=date.today() + timedelta(days=5))
        request = pb.EmptyRequest()
        response = self.servicer.ListImpayes(request, _mock_context())
        self.assertEqual(len(response.impayes), 0)

    def test_list_impayes_exclut_factures_payees(self) -> None:
        """ListImpayes exclut les factures dont le statut est PAYEE."""
        _creer_solde("facture-payee", date_limite=date.today() - timedelta(days=3))
        # Payer la facture
        svc = PaiementService()
        svc.enregistrer_paiement(
            "facture-payee",
            "abonne-001",
            300.0,
            date.today(),
            ModePaiement.ESPECES,
            "",
            "user-001",
        )
        request = pb.EmptyRequest()
        response = self.servicer.ListImpayes(request, _mock_context())
        self.assertEqual(len(response.impayes), 0)


class TestGetSuiviImpayeRPC(TestCase):
    """Tests du RPC GetSuiviImpaye."""

    def setUp(self) -> None:
        with patch("paiements.grpc_server.FacturationServiceClient"):
            self.servicer = PaiementServicer()

    def test_get_suivi_existant(self) -> None:
        """GetSuiviImpaye retourne le suivi d'une facture impayée."""
        from paiements.models import SuiviImpaye

        SuiviImpaye.objects.create(
            facture_id="facture-suivi",
            abonne_id="abonne-001",
            date_depassement=date.today() - timedelta(days=5),
        )
        request = pb.FactureIdRequest(facture_id="facture-suivi")
        response = self.servicer.GetSuiviImpaye(request, _mock_context())
        self.assertEqual(response.facture_id, "facture-suivi")
        self.assertEqual(response.abonne_id, "abonne-001")
        self.assertEqual(response.etape_actuelle, 1)

    def test_get_suivi_inexistant_abort(self) -> None:
        """GetSuiviImpaye avec facture sans suivi provoque un abort NOT_FOUND."""
        request = pb.FactureIdRequest(facture_id="facture-sans-suivi")
        ctx = _mock_context()
        with self.assertRaises(Exception):
            self.servicer.GetSuiviImpaye(request, ctx)
        ctx.abort.assert_called_once()
        self.assertEqual(ctx.abort.call_args[0][0], grpc.StatusCode.NOT_FOUND)
