"""Tests de l'AgregateurDashboard (agrégation + mises à jour)."""

import uuid
from decimal import Decimal

from django.core.exceptions import ObjectDoesNotExist
from django.test import TestCase

from stats.services import AgregateurDashboard


class UpdateStatsCampagneTests(TestCase):
    def setUp(self):
        self.agg = AgregateurDashboard()
        self.cid = str(uuid.uuid4())

    def test_upsert_calcule_attente_et_pourcentage(self):
        stats = self.agg.update_stats_campagne(
            self.cid,
            "Juin 2026",
            total_abonnes=50,
            nb_releves=40,
            consommation_totale=1200.5,
        )
        self.assertEqual(stats.nb_en_attente, 10)
        self.assertEqual(stats.pourcentage_progression, Decimal("80.00"))
        self.assertEqual(stats.consommation_totale, Decimal("1200.5"))

    def test_upsert_idempotent_ecrase_les_valeurs(self):
        self.agg.update_stats_campagne(self.cid, "Juin", 50, 10, 100)
        stats = self.agg.update_stats_campagne(self.cid, "Juin", 50, 45, 900)
        self.assertEqual(stats.nb_releves, 45)
        self.assertEqual(stats.nb_en_attente, 5)

    def test_total_zero_pourcentage_zero(self):
        stats = self.agg.update_stats_campagne(self.cid, "Vide", 0, 0, 0)
        self.assertEqual(stats.pourcentage_progression, Decimal("0"))


class UpdateStatsFacturationTests(TestCase):
    def setUp(self):
        self.agg = AgregateurDashboard()
        self.cid = str(uuid.uuid4())

    def test_generee_incremente_total_montant_et_impayees(self):
        stats = self.agg.update_stats_facturation(
            self.cid, delta_factures=42, delta_montant=210000, type_update="GENEREE"
        )
        self.assertEqual(stats.total_factures, 42)
        self.assertEqual(stats.montant_total_facture, Decimal("210000"))
        self.assertEqual(stats.nb_factures_impayees, 42)

    def test_payee_bascule_impayee_vers_payee(self):
        self.agg.update_stats_facturation(self.cid, 42, 210000, "GENEREE")
        stats = self.agg.update_stats_facturation(self.cid, 3, 0, "PAYEE")
        self.assertEqual(stats.nb_factures_payees, 3)
        self.assertEqual(stats.nb_factures_impayees, 39)

    def test_envoyee(self):
        self.agg.update_stats_facturation(self.cid, 42, 210000, "GENEREE")
        stats = self.agg.update_stats_facturation(self.cid, 40, 0, "ENVOYEE")
        self.assertEqual(stats.nb_factures_envoyees, 40)

    def test_annulee_retire_du_total_et_des_impayees(self):
        """Une régularisation (annulation + facture corrigée) ne doit pas
        compter le montant facturé deux fois."""
        self.agg.update_stats_facturation(self.cid, 10, 100000, "GENEREE")
        stats = self.agg.update_stats_facturation(self.cid, 1, 10000, "ANNULEE", etait_payee=False)
        self.assertEqual(stats.total_factures, 9)
        self.assertEqual(stats.montant_total_facture, Decimal("90000"))
        self.assertEqual(stats.nb_factures_impayees, 9)
        self.assertEqual(stats.nb_factures_payees, 0)

    def test_annulee_d_une_facture_deja_payee_decremente_payees(self):
        self.agg.update_stats_facturation(self.cid, 10, 100000, "GENEREE")
        self.agg.update_stats_facturation(self.cid, 4, 0, "PAYEE")
        stats = self.agg.update_stats_facturation(self.cid, 1, 10000, "ANNULEE", etait_payee=True)
        self.assertEqual(stats.total_factures, 9)
        self.assertEqual(stats.nb_factures_payees, 3)
        self.assertEqual(stats.nb_factures_impayees, 6)

    def test_annulee_ne_touche_pas_les_factures_envoyees(self):
        """Un envoi WhatsApp est un fait du passé, indépendant de l'annulation
        qui a suivi."""
        self.agg.update_stats_facturation(self.cid, 10, 100000, "GENEREE")
        self.agg.update_stats_facturation(self.cid, 10, 0, "ENVOYEE")
        stats = self.agg.update_stats_facturation(self.cid, 1, 10000, "ANNULEE")
        self.assertEqual(stats.nb_factures_envoyees, 10)

    def test_annulee_ne_descend_jamais_sous_zero(self):
        """Filet de sécurité : un flux tronqué (Reporting reconstruit après
        incident) ne doit jamais produire de compteur négatif."""
        stats = self.agg.update_stats_facturation(self.cid, 1, 10000, "ANNULEE")
        self.assertEqual(stats.total_factures, 0)
        self.assertEqual(stats.montant_total_facture, Decimal("0"))
        self.assertEqual(stats.nb_factures_impayees, 0)


class UpdateStatsPaiementsTests(TestCase):
    def setUp(self):
        self.agg = AgregateurDashboard()
        self.cid = str(uuid.uuid4())

    def test_paiement_calcule_taux_et_impaye(self):
        self.agg.update_stats_facturation(self.cid, 10, 100000, "GENEREE")
        stats = self.agg.update_stats_paiements(self.cid, montant_paiement=25000, type_update="PAIEMENT")
        self.assertEqual(stats.montant_encaisse, Decimal("25000"))
        self.assertEqual(stats.montant_impaye, Decimal("75000"))
        self.assertEqual(stats.taux_recouvrement, Decimal("25.00"))

    def test_paiement_cumule(self):
        self.agg.update_stats_facturation(self.cid, 10, 100000, "GENEREE")
        self.agg.update_stats_paiements(self.cid, 25000, "PAIEMENT")
        stats = self.agg.update_stats_paiements(self.cid, 15000, "PAIEMENT")
        self.assertEqual(stats.montant_encaisse, Decimal("40000"))
        self.assertEqual(stats.taux_recouvrement, Decimal("40.00"))


class DashboardTests(TestCase):
    def setUp(self):
        self.agg = AgregateurDashboard()

    def test_dashboard_vide(self):
        d = self.agg.get_dashboard()
        self.assertIsNone(d.campagne)
        self.assertIsNone(d.facturation)
        self.assertIsNone(d.paiements)

    def test_dashboard_retourne_la_campagne_la_plus_recente(self):
        c1 = str(uuid.uuid4())
        c2 = str(uuid.uuid4())
        self.agg.update_stats_campagne(c1, "Mai", 50, 50, 100)
        self.agg.update_stats_campagne(c2, "Juin", 50, 20, 200)  # plus récente
        self.agg.update_stats_facturation(c2, 20, 40000, "GENEREE")
        d = self.agg.get_dashboard()
        self.assertEqual(d.campagne.nom_campagne, "Juin")
        self.assertIsNotNone(d.facturation)
        self.assertEqual(d.facturation.total_factures, 20)

    def test_stats_globales_agrege(self):
        c1, c2 = str(uuid.uuid4()), str(uuid.uuid4())
        self.agg.update_stats_campagne(c1, "Mai", 50, 50, 100)
        self.agg.update_stats_campagne(c2, "Juin", 50, 50, 200)
        self.agg.update_stats_facturation(c1, 50, 250000, "GENEREE")
        self.agg.update_stats_paiements(c1, 100000, "PAIEMENT")
        g = self.agg.get_stats_globales()
        self.assertEqual(len(g.historique_campagnes), 2)
        self.assertEqual(g.consommation_totale_globale, Decimal("300"))
        self.assertEqual(g.montant_total_facture_global, Decimal("250000"))
        self.assertEqual(g.montant_total_encaisse_global, Decimal("100000"))

    def test_stats_campagne_inconnue_leve(self):
        with self.assertRaises(ObjectDoesNotExist):
            self.agg.get_stats_campagne(str(uuid.uuid4()))

    def test_stats_completes_agrege_les_3_domaines(self):
        cid = str(uuid.uuid4())
        self.agg.update_stats_campagne(cid, "Juin", 50, 40, 1200)
        self.agg.update_stats_facturation(cid, 20, 150000, "GENEREE")
        self.agg.update_stats_paiements(cid, 90000, "PAIEMENT")
        d = self.agg.get_stats_completes(cid)
        self.assertEqual(d.campagne.nom_campagne, "Juin")
        self.assertEqual(d.facturation.total_factures, 20)
        self.assertEqual(d.paiements.montant_encaisse, Decimal("90000"))

    def test_stats_completes_campagne_inconnue_ne_leve_pas(self):
        d = self.agg.get_stats_completes(str(uuid.uuid4()))
        self.assertIsNone(d.campagne)
        self.assertIsNone(d.facturation)
        self.assertIsNone(d.paiements)
