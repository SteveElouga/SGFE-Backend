"""Tests du serveur gRPC du Reporting Service (appel direct du servicer)."""

import sys
import uuid
from pathlib import Path
from unittest.mock import MagicMock

from django.conf import settings
from django.core.exceptions import ObjectDoesNotExist
from django.test import TestCase

sys.path.insert(0, str(Path(settings.BASE_DIR) / "proto"))

import reporting_service_pb2 as pb

from stats.grpc_server import ReportingServiceServicer


def _ctx() -> MagicMock:
    return MagicMock()


class ReportingServicerTests(TestCase):
    def setUp(self):
        self.servicer = ReportingServiceServicer()
        self.cid = str(uuid.uuid4())

    def _seed(self):
        self.servicer.UpdateStatsCampagne(
            pb.UpdateStatsCampagneRequest(
                campagne_id=self.cid,
                nom_campagne="Juin 2026",
                total_abonnes=50,
                nb_releves=40,
                consommation_totale=1200,
            ),
            _ctx(),
        )
        self.servicer.UpdateStatsFacturation(
            pb.UpdateStatsFacturationRequest(
                campagne_id=self.cid,
                delta_factures=42,
                delta_montant=210000,
                type_update="GENEREE",
            ),
            _ctx(),
        )
        self.servicer.UpdateStatsPaiements(
            pb.UpdateStatsPaiementsRequest(
                campagne_id=self.cid, montant_paiement=52500, type_update="PAIEMENT"
            ),
            _ctx(),
        )

    def test_update_puis_get_stats_campagne(self):
        self._seed()
        resp = self.servicer.GetStatsCampagne(
            pb.CampagneIdRequest(campagne_id=self.cid), _ctx()
        )
        self.assertEqual(resp.nom_campagne, "Juin 2026")
        self.assertEqual(resp.nb_en_attente, 10)
        self.assertAlmostEqual(resp.pourcentage_progression, 80.0)

    def test_get_dashboard_reflete_les_trois_domaines(self):
        self._seed()
        d = self.servicer.GetDashboard(pb.EmptyRequest(), _ctx())
        self.assertEqual(d.campagne_en_cours.nom_campagne, "Juin 2026")
        self.assertEqual(d.facturation_en_cours.total_factures, 42)
        self.assertAlmostEqual(d.paiements_en_cours.montant_encaisse, 52500.0)
        self.assertAlmostEqual(d.paiements_en_cours.taux_recouvrement, 25.0)

    def test_get_dashboard_vide_retourne_message_vide(self):
        d = self.servicer.GetDashboard(pb.EmptyRequest(), _ctx())
        self.assertEqual(d.campagne_en_cours.nom_campagne, "")
        self.assertEqual(d.facturation_en_cours.total_factures, 0)

    def test_get_stats_globales(self):
        self._seed()
        g = self.servicer.GetStatsGlobales(pb.EmptyRequest(), _ctx())
        self.assertEqual(len(g.historique_campagnes), 1)
        self.assertAlmostEqual(g.montant_total_facture_global, 210000.0)
        self.assertAlmostEqual(g.montant_total_encaisse_global, 52500.0)

    def test_get_stats_campagne_inconnue_leve(self):
        with self.assertRaises(ObjectDoesNotExist):
            self.servicer.GetStatsCampagne(
                pb.CampagneIdRequest(campagne_id=str(uuid.uuid4())), _ctx()
            )

    def test_update_retourne_success(self):
        resp = self.servicer.UpdateStatsCampagne(
            pb.UpdateStatsCampagneRequest(
                campagne_id=self.cid,
                nom_campagne="X",
                total_abonnes=1,
                nb_releves=1,
                consommation_totale=1,
            ),
            _ctx(),
        )
        self.assertTrue(resp.success)
