"""Tests de statsParMois : agrégateur mensuel pur + resolver (fan-out + portée)."""

import sys
from datetime import date
from pathlib import Path
from unittest.mock import MagicMock, patch

from django.conf import settings
from django.test import SimpleTestCase

# Import non qualifié par le paquet `proto` (comme schema/grpc_clients.py) :
# mypy.ini n'exclut de la vérification que les noms de modules nus, pas leur
# forme qualifiée `proto.xxx_pb2` — cette dernière serait analysée pour de
# vrai, et échouerait sur les classes de message générées dynamiquement.
sys.path.insert(0, str(Path(settings.BASE_DIR) / "proto"))

import facturation_service_pb2 as facturation_pb  # noqa: E402
import paiement_service_pb2 as paiement_pb  # noqa: E402

from schema.reporting_types import build_stats_par_mois  # noqa: E402
from schema.stats_queries import StatsQueries  # noqa: E402


def _paiement(montant: float, date_paiement: str, annule: bool = False) -> paiement_pb.PaiementResponse:
    return paiement_pb.PaiementResponse(montant=montant, date_paiement=date_paiement, annule=annule)


def _facture(montant: float, date_generation: str, consommation: float = 0.0) -> facturation_pb.FactureResponse:
    return facturation_pb.FactureResponse(montant=montant, date_generation=date_generation, consommation=consommation)


class TestBuildStatsParMois(SimpleTestCase):
    def test_fenetre_nb_mois_triee_descendante(self) -> None:
        res = build_stats_par_mois([], [], nb_mois=3, today=date(2026, 7, 15))
        self.assertEqual([s.mois for s in res], ["2026-07", "2026-06", "2026-05"])
        self.assertEqual((res[0].annee, res[0].mois_num), (2026, 7))

    def test_bascule_annee(self) -> None:
        res = build_stats_par_mois([], [], nb_mois=3, today=date(2026, 1, 10))
        self.assertEqual([s.mois for s in res], ["2026-01", "2025-12", "2025-11"])

    def test_dissocie_mois_paiement_et_emission(self) -> None:
        # Facture émise en avril (mois-3), payée aujourd'hui (juillet).
        factures = [_facture(10000, "2026-04-02T09:00:00+00:00", consommation=30.0)]
        paiements = [_paiement(10000, "2026-07-15")]
        res = build_stats_par_mois(factures, paiements, nb_mois=6, today=date(2026, 7, 15))
        self.assertEqual(res[0].mois, "2026-07")
        self.assertEqual((res[0].encaisse, res[0].facture), (10000, 0))  # payé, rien émis
        self.assertEqual(res[3].mois, "2026-04")
        self.assertEqual((res[3].facture, res[3].consommation, res[3].encaisse), (10000, 30, 0))

    def test_mois_sans_donnee_a_zero(self) -> None:
        res = build_stats_par_mois([], [], nb_mois=2, today=date(2026, 7, 15))
        self.assertEqual(res[1].mois, "2026-06")
        self.assertEqual((res[1].encaisse, res[1].facture, res[1].nb_paiements, res[1].nb_factures), (0, 0, 0, 0))

    def test_paiement_annule_exclu(self) -> None:
        paiements = [_paiement(5000, "2026-07-10"), _paiement(3000, "2026-07-11", annule=True)]
        res = build_stats_par_mois([], paiements, nb_mois=1, today=date(2026, 7, 15))
        self.assertEqual((res[0].encaisse, res[0].nb_paiements), (5000, 1))

    def test_agrege_le_meme_mois(self) -> None:
        paiements = [_paiement(1000, "2026-07-01"), _paiement(2500, "2026-07-20")]
        res = build_stats_par_mois([], paiements, nb_mois=1, today=date(2026, 7, 15))
        self.assertEqual((res[0].encaisse, res[0].nb_paiements), (3500, 2))

    def test_date_vide_ou_malformee_ignoree(self) -> None:
        # Une date vide (donnée héritée) ne casse pas et n'est comptée nulle part.
        res = build_stats_par_mois([_facture(9999, "")], [_paiement(9999, "")], nb_mois=1, today=date(2026, 7, 15))
        self.assertEqual((res[0].encaisse, res[0].facture, res[0].nb_paiements, res[0].nb_factures), (0, 0, 0, 0))


class TestStatsParMoisResolver(SimpleTestCase):
    @patch("schema.stats_queries.paiement_client")
    @patch("schema.stats_queries.facturation_client")
    @patch("schema.stats_queries.campagne_client")
    @patch("schema.stats_queries.require_role")
    def test_superviseur_scope_ses_campagnes(
        self, mock_role: MagicMock, mock_camp: MagicMock, mock_fac: MagicMock, mock_pai: MagicMock
    ) -> None:
        mock_role.return_value = MagicMock(role="SUPERVISEUR", user_id="sup-1")
        mock_camp.list_campagnes.return_value = MagicMock(campagnes=[MagicMock(campagne_id="c1")])
        mock_fac.get_factures_par_campagne.return_value = MagicMock(factures=[])
        mock_pai.list_paiements_par_campagne.return_value = MagicMock(paiements=[])
        StatsQueries().stats_par_mois(MagicMock(), nb_mois=3)
        mock_camp.list_campagnes.assert_called_once_with(created_by="sup-1")

    @patch("schema.stats_queries.paiement_client")
    @patch("schema.stats_queries.facturation_client")
    @patch("schema.stats_queries.campagne_client")
    @patch("schema.stats_queries.require_role")
    def test_admin_voit_tout(
        self, mock_role: MagicMock, mock_camp: MagicMock, mock_fac: MagicMock, mock_pai: MagicMock
    ) -> None:
        mock_role.return_value = MagicMock(role="ADMIN", user_id="admin-1")
        mock_camp.list_campagnes.return_value = MagicMock(campagnes=[])
        res = StatsQueries().stats_par_mois(MagicMock(), nb_mois=3)
        mock_camp.list_campagnes.assert_called_once_with(created_by="")
        self.assertEqual(len(res), 3)

    @patch("schema.stats_queries.paiement_client")
    @patch("schema.stats_queries.facturation_client")
    @patch("schema.stats_queries.campagne_client")
    @patch("schema.stats_queries.require_role")
    def test_fan_out_agrege_le_mois_courant(
        self, mock_role: MagicMock, mock_camp: MagicMock, mock_fac: MagicMock, mock_pai: MagicMock
    ) -> None:
        mock_role.return_value = MagicMock(role="COMPTABLE", user_id="cpt-1")
        mock_camp.list_campagnes.return_value = MagicMock(campagnes=[MagicMock(campagne_id="c1")])
        mock_fac.get_factures_par_campagne.return_value = MagicMock(factures=[])
        mock_pai.list_paiements_par_campagne.return_value = MagicMock(
            paiements=[_paiement(7500, date.today().isoformat())]
        )
        res = StatsQueries().stats_par_mois(MagicMock(), nb_mois=12)
        self.assertEqual(res[0].mois, date.today().strftime("%Y-%m"))
        self.assertEqual(res[0].encaisse, 7500)
