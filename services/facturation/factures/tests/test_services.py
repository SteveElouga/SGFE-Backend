"""Tests de la logique métier du Facturation Service."""

import datetime
import os
import tempfile
from decimal import Decimal
from io import StringIO
from unittest.mock import patch

from django.core.management import call_command
from django.test import TestCase

from factures.models import Facture, StatutFacture, Tarif
from factures.pdf_generator import InfosSociete, PDF_TEMPLATE_VERSION
from factures.services import FactureService, ReleveData, TarifService
from factures.tests.helpers import service_avec_clients_mockes


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
        self.svc = service_avec_clients_mockes()
        self.tarif_svc = TarifService()
        self.tarif_svc.update_tarif(Decimal("500.00"), datetime.date(2025, 7, 1))
        self.societe = InfosSociete(nom="SGFE Test", adresse="Yaoundé", telephone="+237000000000")

    def _make_releve(self, abonne_id: str = "abo-001", ancien: float = 100.0, nouveau: float = 115.0) -> ReleveData:
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

    def test_generer_factures_ignore_index_decroissant(self):
        """Régression ANO-008 : Facturation doit revalider nouveau_index >=
        ancien_index et ne jamais générer de facture à montant négatif,
        même si un relevé corrompu franchit la validation de Campagne."""
        releves = [
            self._make_releve("abo-001"),  # valide
            self._make_releve("abo-002", ancien=200.0, nouveau=190.0),  # invalide
        ]

        with tempfile.TemporaryDirectory() as tmpdir:
            with patch("factures.services.settings") as mock_settings:
                mock_settings.PDF_STORAGE_DIR = tmpdir
                mock_settings.DEFAULT_DELAI_PAIEMENT_JOURS = 5
                factures = self.svc.generer_factures(
                    campagne_id="camp-index-decroissant",
                    releves=releves,
                    delai_paiement_jours=5,
                    societe=self.societe,
                )

        self.assertEqual(len(factures), 1)
        self.assertEqual(factures[0].abonne_id, "abo-001")

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

    def test_generer_factures_date_releve_datetime(self):
        """Régression ANO-032 : Campagne horodate date_releve (DateTimeField), donc
        date_releve peut arriver en datetime ISO (« ...T17:10:12+00:00 ») via le vrai
        flux SaisirIndex, pas seulement en date. La génération doit en extraire la
        date sans planter (avant : Invalid isoformat string → génération impossible)."""
        releve = ReleveData(
            abonne_id="abo-001",
            ancien_index=100.0,
            nouveau_index=115.0,
            consommation=15.0,
            date_releve="2025-07-15T17:10:12.179407+00:00",
        )
        with tempfile.TemporaryDirectory() as tmpdir:
            with patch("factures.services.settings") as mock_settings:
                mock_settings.PDF_STORAGE_DIR = tmpdir
                factures = self.svc.generer_factures(
                    campagne_id="camp-datetime",
                    releves=[releve],
                    delai_paiement_jours=5,
                    societe=self.societe,
                )

        self.assertEqual(len(factures), 1)
        self.assertEqual(factures[0].date_releve, datetime.date(2025, 7, 15))
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


class GetPdfBytesTests(TestCase):
    """Cache PDF version-aware : régénération si gabarit obsolète, repli si échec."""

    def setUp(self):
        self.svc = service_avec_clients_mockes()
        self.facture = Facture.objects.create(
            numero_facture="FACT-2026-07-0001",
            abonne_id="abo-1",
            campagne_id="camp-1",
            ancien_index=Decimal("100"),
            nouveau_index=Decimal("112"),
            consommation=Decimal("12"),
            prix_m3=Decimal("500"),
            montant=Decimal("6000"),
            date_releve=datetime.date(2026, 7, 1),
            date_limite_paiement=datetime.date(2026, 7, 6),
        )

    def _fichier_pdf(self, contenu: bytes) -> str:
        fd, path = tempfile.mkstemp(suffix=".pdf")
        with os.fdopen(fd, "wb") as f:
            f.write(contenu)
        self.addCleanup(lambda: os.path.exists(path) and os.remove(path))
        return path

    def test_sert_le_cache_si_version_a_jour(self):
        self.facture.pdf_path = self._fichier_pdf(b"%PDF-cache")
        self.facture.pdf_template_version = PDF_TEMPLATE_VERSION
        self.facture.save()

        with patch.object(self.svc, "_regenerer_et_persister") as mock_regen:
            contenu, nom = self.svc.get_pdf_bytes(str(self.facture.id))

        mock_regen.assert_not_called()
        self.assertEqual(contenu, b"%PDF-cache")
        self.assertEqual(nom, "FACT-2026-07-0001.pdf")

    def test_regenere_si_version_obsolete(self):
        self.facture.pdf_path = self._fichier_pdf(b"%PDF-vieux")
        self.facture.pdf_template_version = 0  # antérieur au gabarit courant
        self.facture.save()
        neuf = self._fichier_pdf(b"%PDF-neuf")

        with patch.object(self.svc, "_regenerer_et_persister", return_value=neuf) as mock_regen:
            contenu, _ = self.svc.get_pdf_bytes(str(self.facture.id))

        mock_regen.assert_called_once()
        self.assertEqual(contenu, b"%PDF-neuf")

    def test_repli_sur_pdf_obsolete_si_regeneration_echoue(self):
        self.facture.pdf_path = self._fichier_pdf(b"%PDF-vieux")
        self.facture.pdf_template_version = 0
        self.facture.save()

        with patch.object(self.svc, "_regenerer_et_persister", return_value=""):
            contenu, _ = self.svc.get_pdf_bytes(str(self.facture.id))

        # Repli : on ressert l'ancien PDF plutôt que de ne rien renvoyer.
        self.assertEqual(contenu, b"%PDF-vieux")

    def test_erreur_si_aucun_pdf_et_regeneration_echoue(self):
        self.facture.pdf_path = ""
        self.facture.pdf_template_version = 0
        self.facture.save()

        with patch.object(self.svc, "_regenerer_et_persister", return_value=""):
            with self.assertRaises(FileNotFoundError):
                self.svc.get_pdf_bytes(str(self.facture.id))

    def test_regenerer_pdf_estampille_la_version_courante(self):
        chemin = self._fichier_pdf(b"%PDF-genere")
        with (
            patch.object(self.svc, "_generer_et_sauver_pdf", return_value=chemin),
            patch.object(self.svc._campagne_client, "get_campagne_nom", return_value=""),
        ):
            ok = self.svc.regenerer_pdf(self.facture, societe=InfosSociete(nom="X"))

        self.assertTrue(ok)
        self.facture.refresh_from_db()
        self.assertEqual(self.facture.pdf_template_version, PDF_TEMPLATE_VERSION)
        self.assertEqual(self.facture.pdf_path, chemin)


class RegenererPdfsCommandTests(TestCase):
    """Commande `regenerer_pdfs` — sélection des obsolètes, dry-run, rapport."""

    def _facture_obsolete(self) -> Facture:
        return Facture.objects.create(
            numero_facture="FACT-2026-07-0009",
            abonne_id="abo-9",
            campagne_id="camp-9",
            ancien_index=Decimal("10"),
            nouveau_index=Decimal("20"),
            consommation=Decimal("10"),
            prix_m3=Decimal("500"),
            montant=Decimal("5000"),
            date_releve=datetime.date(2026, 7, 1),
            date_limite_paiement=datetime.date(2026, 7, 6),
            pdf_template_version=0,
        )

    def test_dry_run_liste_sans_regenerer(self):
        self._facture_obsolete()
        out = StringIO()
        with patch.object(FactureService, "regenerer_pdf") as mock_regen:
            call_command("regenerer_pdfs", "--dry-run", stdout=out)

        mock_regen.assert_not_called()
        self.assertIn("dry-run", out.getvalue().lower())

    def test_regenere_les_obsoletes_et_rapporte(self):
        self._facture_obsolete()
        out = StringIO()
        with (
            patch("factures.grpc_clients.ConfigServiceClient"),
            patch.object(FactureService, "regenerer_pdf", return_value=True) as mock_regen,
        ):
            call_command("regenerer_pdfs", stdout=out)

        mock_regen.assert_called_once()
        self.assertIn("1/1 PDF régénérés", out.getvalue())
