"""Tests de la logique métier du Facturation Service."""

import datetime
import tempfile
from decimal import Decimal
from unittest.mock import patch

from django.test import TestCase

from factures.models import StatutFacture, Tarif
from factures.pdf_generator import InfosSociete
from factures.services import FactureService, ReleveData, TarifService


class TarifServiceTests(TestCase):
    def setUp(self):
        self.svc = TarifService()

    def test_update_tarif_cree_tarif_actif(self):
        tarif = self.svc.update_tarif(
            prix_m3=Decimal("500.00"),
            date_effet=datetime.date(2025, 7, 1),
        )
        self.assertTrue(tarif.is_active)
        self.assertEqual(tarif.prix_m3, Decimal("500.00"))

    def test_update_tarif_desactive_ancien(self):
        self.svc.update_tarif(Decimal("400.00"), datetime.date(2025, 1, 1))
        self.svc.update_tarif(Decimal("500.00"), datetime.date(2025, 7, 1))

        anciens = Tarif.objects.filter(is_active=False)
        actifs = Tarif.objects.filter(is_active=True)
        self.assertEqual(anciens.count(), 1)
        self.assertEqual(actifs.count(), 1)
        self.assertEqual(actifs.first().prix_m3, Decimal("500.00"))

    def test_get_tarif_actuel(self):
        self.svc.update_tarif(Decimal("600.00"), datetime.date(2025, 7, 1))
        tarif = self.svc.get_tarif_actuel()
        self.assertEqual(tarif.prix_m3, Decimal("600.00"))

    def test_update_tarif_prix_nul_leve_erreur(self):
        from django.core.exceptions import ValidationError

        with self.assertRaises(ValidationError):
            self.svc.update_tarif(Decimal("0"), datetime.date(2025, 7, 1))

    def test_update_tarif_prix_negatif_leve_erreur(self):
        from django.core.exceptions import ValidationError

        with self.assertRaises(ValidationError):
            self.svc.update_tarif(Decimal("-100"), datetime.date(2025, 7, 1))


class FactureServiceTests(TestCase):
    def setUp(self):
        self.svc = FactureService()
        self.tarif_svc = TarifService()
        self.tarif_svc.update_tarif(Decimal("500.00"), datetime.date(2025, 7, 1))
        self.societe = InfosSociete(
            nom="SGFE Test", adresse="Yaoundé", telephone="+237000000000"
        )

    def _make_releve(
        self, abonne_id: str = "abo-001", ancien: float = 100.0, nouveau: float = 115.0
    ) -> ReleveData:
        return ReleveData(
            abonne_id=abonne_id,
            ancien_index=ancien,
            nouveau_index=nouveau,
            consommation=nouveau - ancien,
            date_releve="2025-07-15",
        )

    def test_generer_factures_cree_factures(self):
        releves = [
            self._make_releve("abo-001"),
            self._make_releve("abo-002", 200.0, 220.0),
        ]

        with tempfile.TemporaryDirectory() as tmpdir:
            with patch("factures.services.settings") as mock_settings:
                mock_settings.PDF_STORAGE_DIR = tmpdir
                mock_settings.DEFAULT_DELAI_PAIEMENT_JOURS = 5
                factures = self.svc.generer_factures(
                    campagne_id="camp-001",
                    releves=releves,
                    delai_paiement_jours=5,
                    societe=self.societe,
                )

        self.assertEqual(len(factures), 2)
        self.assertEqual(factures[0].statut, StatutFacture.IMPAYEE)
        self.assertEqual(factures[0].prix_m3, Decimal("500.00"))

    def test_generer_factures_calcul_montant(self):
        releve = self._make_releve("abo-001", 100.0, 110.0)

        with tempfile.TemporaryDirectory() as tmpdir:
            with patch("factures.services.settings") as mock_settings:
                mock_settings.PDF_STORAGE_DIR = tmpdir
                mock_settings.DEFAULT_DELAI_PAIEMENT_JOURS = 5
                factures = self.svc.generer_factures(
                    campagne_id="camp-002",
                    releves=[releve],
                    delai_paiement_jours=5,
                    societe=self.societe,
                )

        # 10 m³ × 500 FCFA = 5 000 FCFA
        self.assertEqual(factures[0].montant, Decimal("5000.00"))

    def test_generer_factures_date_limite_respectee(self):
        releve = self._make_releve()

        with tempfile.TemporaryDirectory() as tmpdir:
            with patch("factures.services.settings") as mock_settings:
                mock_settings.PDF_STORAGE_DIR = tmpdir
                factures = self.svc.generer_factures(
                    campagne_id="camp-003",
                    releves=[releve],
                    delai_paiement_jours=5,
                    societe=self.societe,
                )

        # date_releve = 2025-07-15, délai = 5 jours → limite = 2025-07-20
        self.assertEqual(factures[0].date_limite_paiement, datetime.date(2025, 7, 20))

    def test_generer_factures_sans_tarif_leve_erreur(self):
        from django.core.exceptions import ValidationError

        Tarif.objects.all().delete()
        with self.assertRaises(ValidationError):
            self.svc.generer_factures(
                campagne_id="camp-004",
                releves=[self._make_releve()],
                delai_paiement_jours=5,
                societe=self.societe,
            )

    def test_generer_factures_numero_sequentiel(self):
        releves = [
            self._make_releve("abo-001"),
            self._make_releve("abo-002", 200.0, 210.0),
        ]

        with tempfile.TemporaryDirectory() as tmpdir:
            with patch("factures.services.settings") as mock_settings:
                mock_settings.PDF_STORAGE_DIR = tmpdir
                factures = self.svc.generer_factures(
                    campagne_id="camp-005",
                    releves=releves,
                    delai_paiement_jours=5,
                    societe=self.societe,
                )

        numeros = [f.numero_facture for f in factures]
        self.assertIn("FACT-2025-07-0001", numeros)
        self.assertIn("FACT-2025-07-0002", numeros)

    def test_update_statut_facture(self):
        releve = self._make_releve()

        with tempfile.TemporaryDirectory() as tmpdir:
            with patch("factures.services.settings") as mock_settings:
                mock_settings.PDF_STORAGE_DIR = tmpdir
                factures = self.svc.generer_factures(
                    campagne_id="camp-006",
                    releves=[releve],
                    delai_paiement_jours=5,
                    societe=self.societe,
                )

        updated = self.svc.update_statut(str(factures[0].id), StatutFacture.PARTIELLE)
        self.assertEqual(updated.statut, StatutFacture.PARTIELLE)

    def test_update_statut_invalide_leve_erreur(self):
        from django.core.exceptions import ValidationError

        releve = self._make_releve()

        with tempfile.TemporaryDirectory() as tmpdir:
            with patch("factures.services.settings") as mock_settings:
                mock_settings.PDF_STORAGE_DIR = tmpdir
                factures = self.svc.generer_factures(
                    campagne_id="camp-007",
                    releves=[releve],
                    delai_paiement_jours=5,
                    societe=self.societe,
                )

        with self.assertRaises(ValidationError):
            self.svc.update_statut(str(factures[0].id), "INCONNU")

    def test_list_factures_filtre_par_campagne(self):
        releve = self._make_releve()

        with tempfile.TemporaryDirectory() as tmpdir:
            with patch("factures.services.settings") as mock_settings:
                mock_settings.PDF_STORAGE_DIR = tmpdir
                self.svc.generer_factures(
                    campagne_id="camp-aaa",
                    releves=[releve],
                    delai_paiement_jours=5,
                    societe=self.societe,
                )
                self.svc.generer_factures(
                    campagne_id="camp-bbb",
                    releves=[self._make_releve("abo-002", 200.0, 210.0)],
                    delai_paiement_jours=5,
                    societe=self.societe,
                )

        result = self.svc.list_factures(campagne_id="camp-aaa")
        self.assertEqual(len(result), 1)
        self.assertEqual(result[0].campagne_id, "camp-aaa")
