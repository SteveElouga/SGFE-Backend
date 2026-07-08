"""Tests du serveur gRPC du Paiement Service.

Les servicers ne gèrent plus les erreurs eux-mêmes : le mapping exception ->
code gRPC est centralisé dans ErrorHandlingInterceptor (testé dans
test_grpc_interceptors.py). Les tests ci-dessous vérifient donc que le
servicer **propage** l'exception métier attendue.
"""

import sys
from datetime import date, timedelta
from decimal import Decimal
from pathlib import Path
from unittest.mock import MagicMock, patch

import grpc
from django.conf import settings
from django.core.exceptions import ObjectDoesNotExist, ValidationError
from django.test import TestCase

sys.path.insert(0, str(Path(settings.BASE_DIR) / "proto"))

import paiement_service_pb2 as pb

from paiements.grpc_server import PaiementServicer
from paiements.models import ModePaiement, SoldeFacture, StatutSolde
from paiements.repositories import SoldeFactureRepository
from paiements.services import PaiementService


def _mock_context() -> MagicMock:
    """Contexte gRPC mocké (l'abort est fait par l'interceptor, pas le servicer)."""
    return MagicMock(spec=grpc.ServicerContext)


def _creer_solde(
    facture_id: str = "facture-001",
    abonne_id: str = "abonne-001",
    montant: float = 300.00,
    date_limite: date | None = None,
    campagne_id: str = "",
) -> SoldeFacture:
    """Crée un SoldeFacture de test."""
    return SoldeFactureRepository().create(
        facture_id=facture_id,
        abonne_id=abonne_id,
        montant_total=Decimal(str(montant)),
        date_limite_paiement=date_limite or date(2026, 7, 31),
        campagne_id=campagne_id,
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

    def test_initialiser_solde_stocke_campagne_id(self) -> None:
        request = pb.InitialiserSoldeRequest(
            facture_id="facture-camp",
            abonne_id="abonne-001",
            montant_total=300.00,
            date_limite_paiement="2026-07-31",
            campagne_id="camp-42",
        )
        self.servicer.InitialiserSolde(request, _mock_context())
        self.assertEqual(SoldeFacture.objects.get(facture_id="facture-camp").campagne_id, "camp-42")

    def test_initialiser_solde_montant_nul_propage_validation_error(self) -> None:
        """Un montant nul propage une ValidationError (-> INVALID_ARGUMENT via interceptor)."""
        request = pb.InitialiserSoldeRequest(
            facture_id="facture-002",
            abonne_id="abonne-001",
            montant_total=0.0,
            date_limite_paiement="2026-07-31",
        )
        with self.assertRaises(ValidationError):
            self.servicer.InitialiserSolde(request, _mock_context())

    def test_initialiser_solde_date_invalide_propage_value_error(self) -> None:
        """Une date mal formatée propage une ValueError (-> INVALID_ARGUMENT via interceptor)."""
        request = pb.InitialiserSoldeRequest(
            facture_id="facture-003",
            abonne_id="abonne-001",
            montant_total=100.00,
            date_limite_paiement="pas-une-date",
        )
        with self.assertRaises(ValueError):
            self.servicer.InitialiserSolde(request, _mock_context())


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

    @patch("paiements.grpc_server.publish_reporting_event")
    @patch("paiements.grpc_server.FacturationServiceClient")
    def test_enregistrer_paiement_publie_stats_reporting(self, mock_fact_cls, mock_pub) -> None:
        _creer_solde("facture-rep", "abonne-001", 300.00, campagne_id="camp-9")
        servicer = PaiementServicer()
        request = pb.EnregistrerPaiementRequest(
            facture_id="facture-rep",
            abonne_id="abonne-001",
            montant=100.00,
            date_paiement="2026-06-20",
            mode_paiement="ESPECES",
            reference_transaction="",
            enregistre_par="user-001",
        )
        servicer.EnregistrerPaiement(request, _mock_context())

        mock_pub.assert_called()
        args, kwargs = mock_pub.call_args_list[0]
        self.assertEqual(args[0], "PAIEMENT_STATS")
        self.assertEqual(kwargs["campagne_id"], "camp-9")
        self.assertEqual(kwargs["type_update"], "PAIEMENT")
        self.assertAlmostEqual(kwargs["montant_paiement"], 100.0)

    @patch("paiements.grpc_server.publish_reporting_event")
    @patch("paiements.grpc_server.FacturationServiceClient")
    def test_enregistrer_paiement_total_emet_impaye_resolu(self, mock_fact_cls, mock_pub) -> None:
        _creer_solde("facture-full", "abonne-001", 100.00, campagne_id="camp-9")
        servicer = PaiementServicer()
        request = pb.EnregistrerPaiementRequest(
            facture_id="facture-full",
            abonne_id="abonne-001",
            montant=100.00,  # solde entièrement payé -> PAYEE
            date_paiement="2026-06-20",
            mode_paiement="ESPECES",
            reference_transaction="",
            enregistre_par="user-001",
        )
        servicer.EnregistrerPaiement(request, _mock_context())

        types = [c.kwargs["type_update"] for c in mock_pub.call_args_list]
        self.assertIn("PAIEMENT", types)
        self.assertIn("IMPAYE_RESOLU", types)

    def test_enregistrer_paiement_montant_invalide_propage_validation_error(self) -> None:
        request = pb.EnregistrerPaiementRequest(
            facture_id="facture-001",
            abonne_id="abonne-001",
            montant=0.0,
            date_paiement="2026-06-20",
            mode_paiement="ESPECES",
            reference_transaction="",
            enregistre_par="user-001",
        )
        with self.assertRaises(ValidationError):
            self.servicer.EnregistrerPaiement(request, _mock_context())

    def test_enregistrer_paiement_surpaiement_propage_validation_error(self) -> None:
        request = pb.EnregistrerPaiementRequest(
            facture_id="facture-001",
            abonne_id="abonne-001",
            montant=500.00,
            date_paiement="2026-06-20",
            mode_paiement="ESPECES",
            reference_transaction="",
            enregistre_par="user-001",
        )
        with self.assertRaises(ValidationError):
            self.servicer.EnregistrerPaiement(request, _mock_context())

    def test_enregistrer_paiement_facture_inconnue_propage_not_found(self) -> None:
        request = pb.EnregistrerPaiementRequest(
            facture_id="facture-inconnue",
            abonne_id="abonne-001",
            montant=100.00,
            date_paiement="2026-06-20",
            mode_paiement="ESPECES",
            reference_transaction="",
            enregistre_par="user-001",
        )
        with self.assertRaises(ObjectDoesNotExist):
            self.servicer.EnregistrerPaiement(request, _mock_context())

    def test_enregistrer_paiement_mobile_money_sans_reference_propage_validation_error(self) -> None:
        request = pb.EnregistrerPaiementRequest(
            facture_id="facture-001",
            abonne_id="abonne-001",
            montant=100.00,
            date_paiement="2026-06-20",
            mode_paiement="MOBILE_MONEY",
            reference_transaction="",
            enregistre_par="user-001",
        )
        with self.assertRaises(ValidationError):
            self.servicer.EnregistrerPaiement(request, _mock_context())


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

    def test_get_solde_facture_inconnue_propage_not_found(self) -> None:
        request = pb.FactureIdRequest(facture_id="facture-inconnue")
        with self.assertRaises(ObjectDoesNotExist):
            self.servicer.GetSolde(request, _mock_context())


class TestListPaiementsRPC(TestCase):
    """Tests du RPC ListPaiements."""

    def setUp(self) -> None:
        with patch("paiements.grpc_server.FacturationServiceClient"):
            self.servicer = PaiementServicer()
        _creer_solde("facture-001", "abonne-001", 500.00)

    def test_list_paiements_retourne_liste(self) -> None:
        """ListPaiements retourne les paiements de la facture."""
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


class TestListPaiementsParCampagneRPC(TestCase):
    """Tests du RPC ListPaiementsParCampagne (export CSV écran 13)."""

    def setUp(self) -> None:
        with patch("paiements.grpc_server.FacturationServiceClient"):
            self.servicer = PaiementServicer()
        _creer_solde("fac-a1", "ab-1", 500.00, campagne_id="camp-A")
        _creer_solde("fac-b1", "ab-2", 200.00, campagne_id="camp-B")

    def test_filtre_les_paiements_de_la_campagne(self) -> None:
        svc = PaiementService()
        svc.enregistrer_paiement("fac-a1", "ab-1", 100.0, date.today(), ModePaiement.ESPECES, "", "u-1")
        svc.enregistrer_paiement("fac-b1", "ab-2", 50.0, date.today(), ModePaiement.ESPECES, "", "u-1")

        response = self.servicer.ListPaiementsParCampagne(pb.CampagneIdRequest(campagne_id="camp-A"), _mock_context())
        self.assertEqual(len(response.paiements), 1)
        self.assertEqual(response.paiements[0].facture_id, "fac-a1")
        self.assertEqual(response.paiements[0].abonne_id, "ab-1")

    def test_campagne_sans_paiement_retourne_vide(self) -> None:
        response = self.servicer.ListPaiementsParCampagne(pb.CampagneIdRequest(campagne_id="camp-A"), _mock_context())
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

    def test_get_suivi_inexistant_propage_not_found(self) -> None:
        request = pb.FactureIdRequest(facture_id="facture-sans-suivi")
        with self.assertRaises(ObjectDoesNotExist):
            self.servicer.GetSuiviImpaye(request, _mock_context())
