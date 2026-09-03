"""Tests de l'avoir : crédit manuel (rectification), journal des mouvements, RPC."""

import sys
from datetime import date
from decimal import Decimal
from pathlib import Path
from unittest.mock import MagicMock, patch

import grpc
from django.conf import settings
from django.core.exceptions import ValidationError
from django.test import TestCase

sys.path.insert(0, str(Path(settings.BASE_DIR) / "proto"))

import paiement_service_pb2 as pb

from paiements.grpc_server import PaiementServicer
from paiements.models import ModePaiement, MouvementAvoir, SoldeFacture, TypeMouvementAvoir
from paiements.repositories import SoldeFactureRepository
from paiements.services import PaiementService


def _mock_context() -> MagicMock:
    return MagicMock(spec=grpc.ServicerContext)


def _creer_solde(facture_id: str, abonne_id: str = "abonne-001", montant_total: float = 300.00) -> SoldeFacture:
    return SoldeFactureRepository().create(
        facture_id=facture_id,
        abonne_id=abonne_id,
        montant_total=Decimal(str(montant_total)),
        date_limite_paiement=date(2026, 7, 1),
    )


class TestCrediterAvoirManuel(TestCase):
    def setUp(self) -> None:
        self.svc = PaiementService()

    def test_credit_manuel_alimente_avoir_et_journalise(self) -> None:
        avoir = self.svc.crediter_avoir_manuel("abonne-001", 1500.0, "Erreur d'index corrigée", "admin-1")
        self.assertEqual(avoir.montant, Decimal("1500.00"))
        m = MouvementAvoir.objects.get(abonne_id="abonne-001")
        self.assertEqual(m.type_mouvement, TypeMouvementAvoir.RECTIFICATION)
        self.assertEqual(m.motif, "Erreur d'index corrigée")
        self.assertEqual(m.cree_par, "admin-1")

    def test_motif_obligatoire(self) -> None:
        with self.assertRaises(ValidationError):
            self.svc.crediter_avoir_manuel("abonne-001", 1000.0, "   ", "admin-1")

    def test_montant_doit_etre_positif(self) -> None:
        with self.assertRaises(ValidationError):
            self.svc.crediter_avoir_manuel("abonne-001", 0.0, "motif", "admin-1")

    def test_get_avoir_abonne_solde_et_mouvements(self) -> None:
        self.svc.crediter_avoir_manuel("abonne-001", 500.0, "geste 1", "admin-1")
        self.svc.crediter_avoir_manuel("abonne-001", 200.0, "geste 2", "admin-1")
        montant, mouvements = self.svc.get_avoir_abonne("abonne-001")
        self.assertEqual(montant, Decimal("700.00"))
        self.assertEqual(len(mouvements), 2)

    def test_get_avoir_abonne_inexistant(self) -> None:
        montant, mouvements = self.svc.get_avoir_abonne("inconnu")
        self.assertEqual(montant, Decimal("0"))
        self.assertEqual(mouvements, [])


class TestJournalisationAutomatique(TestCase):
    def setUp(self) -> None:
        self.svc = PaiementService()

    def test_trop_percu_journalise_un_credit(self) -> None:
        _creer_solde("facture-001", "abonne-001", 300.00)
        self.svc.enregistrer_paiement(
            facture_id="facture-001",
            abonne_id="abonne-001",
            montant=400.0,
            date_paiement=date(2026, 6, 20),
            mode_paiement=ModePaiement.ESPECES,
            reference_transaction="",
            enregistre_par="user-001",
        )
        m = MouvementAvoir.objects.get(abonne_id="abonne-001")
        self.assertEqual(m.type_mouvement, TypeMouvementAvoir.TROP_PERCU)
        self.assertEqual(m.montant, Decimal("100.00"))

    def test_imputation_journalise_un_debit(self) -> None:
        _creer_solde("facture-001", "abonne-001", 300.00)
        self.svc.enregistrer_paiement(
            facture_id="facture-001",
            abonne_id="abonne-001",
            montant=400.0,
            date_paiement=date(2026, 6, 20),
            mode_paiement=ModePaiement.ESPECES,
            reference_transaction="",
            enregistre_par="user-001",
        )
        self.svc.initialiser_solde(
            facture_id="facture-002",
            abonne_id="abonne-001",
            montant_total=60.0,
            date_limite_paiement=date(2026, 8, 1),
        )
        imputations = MouvementAvoir.objects.filter(
            abonne_id="abonne-001", type_mouvement=TypeMouvementAvoir.IMPUTATION
        )
        self.assertEqual(imputations.count(), 1)
        imputation = imputations.first()
        assert imputation is not None
        self.assertEqual(imputation.montant, Decimal("60.00"))
        self.assertEqual(imputation.facture_id, "facture-002")


class TestAvoirRPC(TestCase):
    def setUp(self) -> None:
        with patch("paiements.grpc_server.FacturationServiceClient"):
            self.servicer = PaiementServicer()

    def test_crediter_avoir_puis_lecture(self) -> None:
        req = pb.CrediterAvoirRequest(abonne_id="abonne-001", montant=1500.0, motif="rectif", cree_par="admin-1")
        resp = self.servicer.CrediterAvoir(req, _mock_context())
        self.assertEqual(resp.abonne_id, "abonne-001")
        self.assertAlmostEqual(resp.montant, 1500.0)
        self.assertEqual(len(resp.mouvements), 1)
        self.assertEqual(resp.mouvements[0].type_mouvement, "RECTIFICATION")
        get = self.servicer.GetAvoirAbonne(pb.AbonneIdRequest(abonne_id="abonne-001"), _mock_context())
        self.assertAlmostEqual(get.montant, 1500.0)

    def test_crediter_avoir_motif_vide_propage_validation_error(self) -> None:
        req = pb.CrediterAvoirRequest(abonne_id="abonne-001", montant=100.0, motif="", cree_par="admin-1")
        with self.assertRaises(ValidationError):
            self.servicer.CrediterAvoir(req, _mock_context())
