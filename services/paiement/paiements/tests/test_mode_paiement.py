"""Tests des modes de paiement acceptés (CHÈQUE ajouté, modes invalides rejetés)."""

from datetime import date
from decimal import Decimal

from django.core.exceptions import ValidationError
from django.test import TestCase

from paiements.models import ModePaiement
from paiements.repositories import SoldeFactureRepository
from paiements.services import PaiementService


class TestModesPaiement(TestCase):
    def setUp(self) -> None:
        self.svc = PaiementService()
        SoldeFactureRepository().create(
            facture_id="facture-001",
            abonne_id="abonne-001",
            montant_total=Decimal("300.00"),
            date_limite_paiement=date(2026, 7, 1),
        )

    def _payer(self, mode: str, reference: str = ""):
        return self.svc.enregistrer_paiement(
            facture_id="facture-001",
            abonne_id="abonne-001",
            montant=100.00,
            date_paiement=date(2026, 6, 20),
            mode_paiement=mode,
            reference_transaction=reference,
            enregistre_par="user-001",
        )

    def test_cheque_accepte(self) -> None:
        paiement, solde = self._payer(ModePaiement.CHEQUE, reference="CHQ-4412")
        self.assertEqual(paiement.mode_paiement, ModePaiement.CHEQUE)
        self.assertEqual(solde.montant_paye, Decimal("100.00"))

    def test_especes_toujours_accepte(self) -> None:
        paiement, _ = self._payer(ModePaiement.ESPECES)
        self.assertEqual(paiement.mode_paiement, ModePaiement.ESPECES)

    def test_mode_inconnu_rejete(self) -> None:
        with self.assertRaises(ValidationError):
            self._payer("BITCOIN")

    def test_mode_avoir_rejete_en_saisie_manuelle(self) -> None:
        """AVOIR est réservé à l'imputation interne — refusé en saisie manuelle."""
        with self.assertRaises(ValidationError):
            self._payer(ModePaiement.AVOIR)
