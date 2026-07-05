"""Tests de la synthèse de campagne (contexte pur + orchestration SyntheseCampagneService)."""

import datetime
from types import SimpleNamespace
from unittest.mock import patch

from django.core.exceptions import ObjectDoesNotExist
from django.test import SimpleTestCase

from factures.pdf_generator import InfosSociete
from factures.services import SyntheseCampagneService
from factures.synthese_generator import build_synthese_context


def _stats_completes() -> dict:
    return {
        "campagne": {
            "campagne_id": "camp-1",
            "nom_campagne": "Juin 2026",
            "total_abonnes": 50,
            "nb_releves": 40,
            "nb_en_attente": 10,
            "pourcentage_progression": 80.0,
            "consommation_totale": 1200,
        },
        "facturation": {
            "total_factures": 40,
            "montant_total_facture": 250000,
            "nb_factures_envoyees": 38,
            "nb_factures_payees": 30,
            "nb_impayes": 10,
        },
        "paiements": {
            "montant_encaisse": 180000,
            "montant_impaye": 70000,
            "nb_impayes": 10,
            "taux_recouvrement": 72.0,
        },
    }


class BuildSyntheseContextTests(SimpleTestCase):
    def test_contexte_formate_les_3_blocs(self):
        ctx = build_synthese_context(
            _stats_completes(), InfosSociete(nom="Hydro CI"), "camp-1", datetime.date(2026, 7, 5)
        )
        self.assertEqual(ctx["nom_campagne"], "Juin 2026")
        self.assertEqual(ctx["date_edition"], "05/07/2026")
        self.assertEqual(ctx["numero_synthese"], "SYNTH-2026-07-05")
        self.assertEqual(ctx["campagne"]["pourcentage_progression"], "80,0 %")
        self.assertEqual(ctx["campagne"]["consommation_totale"], "1 200 m³")
        self.assertEqual(ctx["facturation"]["montant_total_facture"], "250 000 FCFA")
        self.assertEqual(ctx["paiements"]["taux_recouvrement"], "72,0 %")

    def test_blocs_absents_affiches_a_zero(self):
        # Reporting ne renvoie que le bloc campagne (facturation/paiements = None).
        stats = {"campagne": _stats_completes()["campagne"], "facturation": None, "paiements": None}
        ctx = build_synthese_context(stats, InfosSociete(), "camp-1", datetime.date(2026, 7, 5))
        self.assertEqual(ctx["facturation"]["total_factures"], "0")
        self.assertEqual(ctx["paiements"]["montant_encaisse"], "0 FCFA")

    def test_repli_sur_id_si_nom_campagne_absent(self):
        stats = {"campagne": {"campagne_id": "camp-1"}, "facturation": None, "paiements": None}
        ctx = build_synthese_context(stats, InfosSociete(), "camp-1", datetime.date(2026, 7, 5))
        self.assertEqual(ctx["nom_campagne"], "camp-1")


class SyntheseCampagneServiceTests(SimpleTestCase):
    def _service(self, reporting):
        config = SimpleNamespace(get_infos_societe=lambda: InfosSociete(nom="Hydro CI"))
        return SyntheseCampagneService(reporting_client=reporting, config_client=config)

    def test_generer_synthese_retourne_bytes(self):
        reporting = SimpleNamespace(get_stats_completes=lambda cid: _stats_completes())
        svc = self._service(reporting)
        with patch(
            "factures.synthese_generator.generer_synthese_pdf_bytes", return_value=b"%PDF-1.4 synth"
        ) as mock_gen:
            pdf_bytes, filename = svc.generer_synthese_campagne_pdf("camp-1")
        self.assertEqual(pdf_bytes, b"%PDF-1.4 synth")
        self.assertTrue(filename.startswith("synthese-camp-1-"))
        contexte = mock_gen.call_args.args[0]
        self.assertEqual(contexte["nom_campagne"], "Juin 2026")

    def test_reporting_injoignable_leve_object_does_not_exist(self):
        reporting = SimpleNamespace(get_stats_completes=lambda cid: None)
        svc = self._service(reporting)
        with self.assertRaises(ObjectDoesNotExist):
            svc.generer_synthese_campagne_pdf("camp-1")

    def test_campagne_sans_stats_leve_object_does_not_exist(self):
        reporting = SimpleNamespace(
            get_stats_completes=lambda cid: {"campagne": None, "facturation": None, "paiements": None}
        )
        svc = self._service(reporting)
        with self.assertRaises(ObjectDoesNotExist):
            svc.generer_synthese_campagne_pdf("camp-1")
