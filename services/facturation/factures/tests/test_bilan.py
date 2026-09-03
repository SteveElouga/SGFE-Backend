"""Tests du bilan des impayés (contexte pur + orchestration BilanImpayesService)."""

import datetime
from decimal import Decimal
from types import SimpleNamespace
from typing import Any
from unittest.mock import patch

from django.test import TestCase

from factures.bilan_generator import LigneImpaye, build_bilan_context
from factures.models import Facture, StatutFacture
from factures.pdf_generator import InfosSociete
from factures.services import BilanImpayesService


def _ligne(**kw: Any) -> LigneImpaye:
    defaults: dict[str, Any] = dict(
        nom_complet="Traoré Seydou",
        numero_abonne="AB-0008",
        numero_facture="FACT-2026-06-0008",
        montant=15000,
        paye=0,
        solde=15000,
        jours_retard=12,
        etape=4,
        en_pause=False,
    )
    defaults.update(kw)
    return LigneImpaye(**defaults)


class BuildBilanContextTests(TestCase):
    def test_synthese_et_totaux(self) -> None:
        lignes = [
            _ligne(etape=4, solde=15000, jours_retard=12),
            _ligne(etape=3, solde=10750, paye=10750, montant=21500, en_pause=True, jours_retard=7),
            _ligne(etape=2, solde=9000, paye=9000, montant=18000, jours_retard=4),
            _ligne(etape=1, solde=8000, jours_retard=1),
        ]
        ctx = build_bilan_context(lignes, InfosSociete(nom="Hydro CI"), datetime.date(2026, 7, 4))

        self.assertEqual(ctx["nb_impayes"], 4)
        self.assertEqual(ctx["nb_etape3_plus"], 2)  # étapes 4 et 3
        self.assertEqual(ctx["nb_suspendus"], 1)  # étape 4
        self.assertEqual(ctx["total_solde"], "42 750")
        self.assertEqual(ctx["numero_bilan"], "BILAN-IMP-2026-07-04")
        self.assertEqual(ctx["date_arrete"], "04/07/2026")

    def test_badge_pause_pour_acompte(self) -> None:
        ctx = build_bilan_context([_ligne(en_pause=True, etape=3)], InfosSociete(), datetime.date(2026, 7, 4))
        self.assertEqual(ctx["lignes"][0]["badge"], "Pause · acompte reçu")

    def test_badge_etape_sans_pause(self) -> None:
        ctx = build_bilan_context([_ligne(en_pause=False, etape=2)], InfosSociete(), datetime.date(2026, 7, 4))
        self.assertEqual(ctx["lignes"][0]["badge"], "Étape 2 · Rappel ferme")

    def test_repartition_par_etape(self) -> None:
        lignes = [_ligne(etape=1, solde=5000), _ligne(etape=1, solde=5000), _ligne(etape=4, solde=10000)]
        ctx = build_bilan_context(lignes, InfosSociete(), datetime.date(2026, 7, 4))
        rep = {r["label"]: r for r in ctx["repartition"]}
        self.assertEqual(rep["Étape 1 · Rappel doux"]["nb"], 2)
        self.assertEqual(rep["Étape 1 · Rappel doux"]["pct"], 50.0)
        self.assertEqual(rep["Étape 4 · Suspendue"]["pct"], 50.0)

    def test_bilan_vide(self) -> None:
        ctx = build_bilan_context([], InfosSociete(), datetime.date(2026, 7, 4))
        self.assertEqual(ctx["nb_impayes"], 0)
        self.assertEqual(ctx["total_solde"], "0")
        self.assertEqual(ctx["repartition"], [])


class BilanImpayesServiceTests(TestCase):
    def setUp(self) -> None:
        self.facture = Facture.objects.create(
            numero_facture="FACT-2026-06-0008",
            abonne_id="abonne-8",
            campagne_id="camp-1",
            ancien_index=Decimal("100"),
            nouveau_index=Decimal("130"),
            consommation=Decimal("30"),
            prix_m3=Decimal("500"),
            montant=Decimal("15000"),
            statut=StatutFacture.IMPAYEE,
            date_releve=datetime.date(2026, 6, 15),
            date_limite_paiement=datetime.date(2026, 6, 20),
        )

    def _service(self) -> BilanImpayesService:
        paiement = SimpleNamespace(
            list_impayes=lambda: [
                {
                    "facture_id": str(self.facture.id),
                    "montant_total": 15000.0,
                    "montant_paye": 0.0,
                    "solde_restant": 15000.0,
                    "statut": "IMPAYEE",
                }
            ],
            get_suivi_impaye=lambda fid: {
                "etape_actuelle": 4,
                "date_depassement": "2026-06-22",
                "resolu_le": "",
            },
        )
        abonne = SimpleNamespace(
            get_abonne=lambda aid: SimpleNamespace(prenom="Seydou", nom="Traoré", numero_abonne="AB-0008")
        )
        config = SimpleNamespace(get_infos_societe=lambda: InfosSociete(nom="Hydro CI"))
        return BilanImpayesService(paiement_client=paiement, abonne_client=abonne, config_client=config)

    def test_build_ligne_enrichit_et_calcule_le_retard(self) -> None:
        svc = self._service()
        ligne = svc._build_ligne(
            {
                "facture_id": str(self.facture.id),
                "montant_total": 15000.0,
                "montant_paye": 0.0,
                "solde_restant": 15000.0,
            },
            datetime.date(2026, 7, 4),
        )
        self.assertEqual(ligne.nom_complet, "Seydou Traoré")
        self.assertEqual(ligne.numero_abonne, "AB-0008")
        self.assertEqual(ligne.numero_facture, "FACT-2026-06-0008")
        self.assertEqual(ligne.etape, 4)
        self.assertEqual(ligne.jours_retard, 12)  # 04/07 - 22/06
        self.assertFalse(ligne.en_pause)

    def test_en_pause_si_acompte(self) -> None:
        svc = self._service()
        ligne = svc._build_ligne(
            {
                "facture_id": str(self.facture.id),
                "montant_total": 21500.0,
                "montant_paye": 10750.0,
                "solde_restant": 10750.0,
            },
            datetime.date(2026, 7, 4),
        )
        self.assertTrue(ligne.en_pause)

    def test_generer_bilan_impayes_pdf_retourne_bytes(self) -> None:
        svc = self._service()
        with patch("factures.bilan_generator.generer_bilan_pdf_bytes", return_value=b"%PDF-1.4 bilan") as mock_gen:
            pdf_bytes, filename = svc.generer_bilan_impayes_pdf()
        self.assertEqual(pdf_bytes, b"%PDF-1.4 bilan")
        self.assertTrue(filename.startswith("bilan-impayes-"))
        # Le contexte passé au générateur contient bien la ligne enrichie.
        contexte = mock_gen.call_args.args[0]
        self.assertEqual(contexte["nb_impayes"], 1)
        self.assertEqual(contexte["lignes"][0]["numero_abonne"], "AB-0008")
