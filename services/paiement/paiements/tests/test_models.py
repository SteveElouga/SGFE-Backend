"""Tests unitaires des modèles du Paiement Service."""

import uuid
from datetime import date
from decimal import Decimal

from django.test import TestCase

from paiements.models import (
    ModePaiement,
    Paiement,
    SoldeFacture,
    StatutSolde,
    SuiviImpaye,
)


class TestPaiementModel(TestCase):
    """Tests du modèle Paiement."""

    def test_creation_paiement_especes(self) -> None:
        """Crée un paiement en espèces et vérifie les champs."""
        p = Paiement.objects.create(
            facture_id="facture-001",
            abonne_id="abonne-001",
            montant=Decimal("150.00"),
            date_paiement=date(2026, 6, 15),
            mode_paiement=ModePaiement.ESPECES,
            enregistre_par="user-001",
        )
        self.assertIsNotNone(p.id)
        self.assertEqual(p.facture_id, "facture-001")
        self.assertEqual(p.montant, Decimal("150.00"))
        self.assertEqual(p.mode_paiement, ModePaiement.ESPECES)
        self.assertEqual(p.reference_transaction, "")
        self.assertIsNotNone(p.created_at)

    def test_creation_paiement_mobile_money(self) -> None:
        """Crée un paiement Mobile Money avec référence de transaction."""
        p = Paiement.objects.create(
            facture_id="facture-002",
            abonne_id="abonne-002",
            montant=Decimal("200.00"),
            date_paiement=date(2026, 6, 16),
            mode_paiement=ModePaiement.MOBILE_MONEY,
            reference_transaction="TXN-12345",
            enregistre_par="user-002",
        )
        self.assertEqual(p.mode_paiement, ModePaiement.MOBILE_MONEY)
        self.assertEqual(p.reference_transaction, "TXN-12345")

    def test_creation_paiement_virement(self) -> None:
        """Crée un paiement par virement avec référence."""
        p = Paiement.objects.create(
            facture_id="facture-003",
            abonne_id="abonne-003",
            montant=Decimal("500.00"),
            date_paiement=date(2026, 6, 17),
            mode_paiement=ModePaiement.VIREMENT,
            reference_transaction="VIR-67890",
            enregistre_par="user-003",
        )
        self.assertEqual(p.mode_paiement, ModePaiement.VIREMENT)
        self.assertEqual(p.reference_transaction, "VIR-67890")

    def test_paiement_pk_est_uuid(self) -> None:
        """Vérifie que la PK est un UUID valide."""
        p = Paiement.objects.create(
            facture_id="facture-004",
            abonne_id="abonne-004",
            montant=Decimal("100.00"),
            date_paiement=date.today(),
            mode_paiement=ModePaiement.ESPECES,
            enregistre_par="user-001",
        )
        self.assertIsInstance(p.id, uuid.UUID)

    def test_paiement_str(self) -> None:
        """Vérifie la représentation string d'un paiement."""
        p = Paiement.objects.create(
            facture_id="facture-005",
            abonne_id="abonne-005",
            montant=Decimal("75.00"),
            date_paiement=date.today(),
            mode_paiement=ModePaiement.ESPECES,
            enregistre_par="user-001",
        )
        self.assertIn("facture-005", str(p))


class TestSoldeFactureModel(TestCase):
    """Tests du modèle SoldeFacture."""

    def test_creation_solde_initial(self) -> None:
        """Crée un solde initial et vérifie les valeurs par défaut."""
        s = SoldeFacture.objects.create(
            facture_id="facture-001",
            abonne_id="abonne-001",
            montant_total=Decimal("300.00"),
            montant_paye=Decimal("0.00"),
            solde_restant=Decimal("300.00"),
            statut=StatutSolde.IMPAYEE,
            date_limite_paiement=date(2026, 7, 1),
        )
        self.assertEqual(s.facture_id, "facture-001")
        self.assertEqual(s.statut, StatutSolde.IMPAYEE)
        self.assertEqual(s.montant_paye, Decimal("0.00"))
        self.assertEqual(s.solde_restant, Decimal("300.00"))
        self.assertIsNotNone(s.updated_at)

    def test_solde_pk_est_facture_id(self) -> None:
        """Vérifie que la PK est le facture_id (une ligne par facture)."""
        SoldeFacture.objects.create(
            facture_id="facture-pk-test",
            abonne_id="abonne-001",
            montant_total=Decimal("100.00"),
            montant_paye=Decimal("0.00"),
            solde_restant=Decimal("100.00"),
            statut=StatutSolde.IMPAYEE,
            date_limite_paiement=date.today(),
        )
        s = SoldeFacture.objects.get(pk="facture-pk-test")
        self.assertEqual(s.facture_id, "facture-pk-test")

    def test_statut_choices(self) -> None:
        """Vérifie que les trois statuts sont accessibles."""
        self.assertEqual(StatutSolde.IMPAYEE, "IMPAYEE")
        self.assertEqual(StatutSolde.PARTIELLE, "PARTIELLE")
        self.assertEqual(StatutSolde.PAYEE, "PAYEE")

    def test_solde_str(self) -> None:
        """Vérifie la représentation string d'un solde."""
        s = SoldeFacture.objects.create(
            facture_id="facture-str-test",
            abonne_id="abonne-001",
            montant_total=Decimal("200.00"),
            montant_paye=Decimal("0.00"),
            solde_restant=Decimal("200.00"),
            statut=StatutSolde.IMPAYEE,
            date_limite_paiement=date.today(),
        )
        self.assertIn("facture-str-test", str(s))


class TestSuiviImpayeModel(TestCase):
    """Tests du modèle SuiviImpaye."""

    def test_creation_suivi_impaye(self) -> None:
        """Crée un suivi impayé et vérifie les valeurs par défaut."""
        s = SuiviImpaye.objects.create(
            facture_id="facture-001",
            abonne_id="abonne-001",
            date_depassement=date(2026, 6, 1),
        )
        self.assertIsNotNone(s.id)
        self.assertEqual(s.etape_actuelle, 1)
        self.assertFalse(s.rappel_1_envoye)
        self.assertFalse(s.rappel_2_envoye)
        self.assertFalse(s.avertissement_envoye)
        self.assertFalse(s.suspension_effectuee)
        self.assertIsNone(s.resolu_le)
        self.assertIsNone(s.relances_suspendues_jusqu)

    def test_suivi_facture_id_unique(self) -> None:
        """Vérifie la contrainte d'unicité sur facture_id."""
        from django.db import IntegrityError

        SuiviImpaye.objects.create(
            facture_id="facture-unique-test",
            abonne_id="abonne-001",
            date_depassement=date.today(),
        )
        with self.assertRaises(IntegrityError):
            SuiviImpaye.objects.create(
                facture_id="facture-unique-test",
                abonne_id="abonne-002",
                date_depassement=date.today(),
            )

    def test_suivi_pk_est_uuid(self) -> None:
        """Vérifie que la PK est un UUID."""
        s = SuiviImpaye.objects.create(
            facture_id="facture-uuid-test",
            abonne_id="abonne-001",
            date_depassement=date.today(),
        )
        self.assertIsInstance(s.id, uuid.UUID)

    def test_suivi_str(self) -> None:
        """Vérifie la représentation string d'un suivi."""
        s = SuiviImpaye.objects.create(
            facture_id="facture-str-test",
            abonne_id="abonne-001",
            date_depassement=date.today(),
        )
        self.assertIn("facture-str-test", str(s))
