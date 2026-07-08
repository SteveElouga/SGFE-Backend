"""Tests du serveur gRPC du Facturation Service."""

import datetime
import tempfile
from decimal import Decimal
from unittest.mock import MagicMock, patch

import grpc
from django.core.exceptions import ObjectDoesNotExist, ValidationError
from django.test import TestCase

from factures.exceptions import PreconditionError
from factures.models import StatutFacture, Tarif
from factures.pdf_generator import InfosSociete
from factures.services import TarifService


def _make_context():
    # Le mapping exception -> abort est fait par l'interceptor (testé dans
    # test_grpc_interceptors.py) : le servicer propage l'exception métier.
    return MagicMock()


class GetTarifActuelTests(TestCase):
    def setUp(self):
        from factures.grpc_server import FacturationServicer

        self.servicer = FacturationServicer.__new__(FacturationServicer)
        self.servicer._tarif_svc = TarifService()
        self.servicer._facture_svc = MagicMock()
        self.servicer._campagne_client = MagicMock()
        self.servicer._config_client = MagicMock()

    def _pb(self):
        import sys
        from pathlib import Path

        from django.conf import settings

        sys.path.insert(0, str(Path(settings.BASE_DIR) / "proto"))
        import facturation_service_pb2 as pb

        return pb

    def test_get_tarif_actuel_succes(self):
        TarifService().update_tarif(Decimal("500.00"), datetime.date(2025, 7, 1))
        pb = self._pb()
        response = self.servicer.GetTarifActuel(pb.EmptyRequest(), MagicMock())
        self.assertAlmostEqual(response.prix_m3, 500.0)
        self.assertTrue(response.is_active)

    def test_get_tarif_actuel_absent_propage_not_found(self):
        Tarif.objects.all().delete()
        pb = self._pb()
        with self.assertRaises(ObjectDoesNotExist):
            self.servicer.GetTarifActuel(pb.EmptyRequest(), _make_context())

    def test_update_tarif_succes(self):
        pb = self._pb()
        request = pb.UpdateTarifRequest(prix_m3=600.0, date_effet="2025-08-01")
        response = self.servicer.UpdateTarif(request, MagicMock())
        self.assertAlmostEqual(response.prix_m3, 600.0)
        self.assertTrue(response.is_active)

    def test_update_tarif_prix_invalide_propage_validation_error(self):
        pb = self._pb()
        request = pb.UpdateTarifRequest(prix_m3=0.0, date_effet="2025-08-01")
        with self.assertRaises(ValidationError):
            self.servicer.UpdateTarif(request, _make_context())


class GenererFacturesTests(TestCase):
    def setUp(self):
        from factures.grpc_server import FacturationServicer

        self.servicer = FacturationServicer.__new__(FacturationServicer)
        from factures.tests.helpers import service_avec_clients_mockes

        self.servicer._tarif_svc = TarifService()
        self.servicer._facture_svc = service_avec_clients_mockes()
        self.servicer._campagne_client = MagicMock()
        self.servicer._config_client = MagicMock()
        self.servicer._config_client.get_delai_paiement_jours.return_value = 5
        self.servicer._config_client.get_infos_societe.return_value = InfosSociete(nom="SGFE")

        TarifService().update_tarif(Decimal("500.00"), datetime.date(2025, 7, 1))

    def _pb(self):
        import sys
        from pathlib import Path

        from django.conf import settings

        sys.path.insert(0, str(Path(settings.BASE_DIR) / "proto"))
        import facturation_service_pb2 as pb

        return pb

    def test_generer_factures_succes(self):
        self.servicer._campagne_client.list_releves.return_value = [
            {
                "abonne_id": "abo-001",
                "ancien_index": 100.0,
                "nouveau_index": 115.0,
                "consommation": 15.0,
                "date_releve": "2025-07-15",
                "statut": "RELEVE",
            }
        ]
        pb = self._pb()
        with tempfile.TemporaryDirectory() as tmpdir:
            with patch("factures.services.settings") as mock_settings:
                mock_settings.PDF_STORAGE_DIR = tmpdir
                response = self.servicer.GenererFactures(pb.GenererFacturesRequest(campagne_id="camp-001"), MagicMock())
        self.assertEqual(len(response.factures), 1)
        self.assertAlmostEqual(response.factures[0].montant, 7500.0)

    def test_generer_factures_campagne_service_ko_propage_rpc_error(self):
        self.servicer._campagne_client.list_releves.side_effect = grpc.RpcError()
        pb = self._pb()
        with self.assertRaises(grpc.RpcError):
            self.servicer.GenererFactures(pb.GenererFacturesRequest(campagne_id="camp-002"), _make_context())

    def test_generer_factures_sans_tarif_propage_precondition_error(self):
        Tarif.objects.all().delete()
        self.servicer._campagne_client.list_releves.return_value = [
            {
                "abonne_id": "abo-001",
                "ancien_index": 100.0,
                "nouveau_index": 115.0,
                "consommation": 15.0,
                "date_releve": "2025-07-15",
                "statut": "RELEVE",
            }
        ]
        pb = self._pb()
        # PreconditionError -> FAILED_PRECONDITION via l'interceptor.
        with self.assertRaises(PreconditionError):
            with tempfile.TemporaryDirectory() as tmpdir:
                with patch("factures.services.settings") as mock_settings:
                    mock_settings.PDF_STORAGE_DIR = tmpdir
                    self.servicer.GenererFactures(pb.GenererFacturesRequest(campagne_id="camp-003"), _make_context())


class UpdateStatutFactureTests(TestCase):
    def setUp(self):
        from factures.grpc_server import FacturationServicer
        from factures.tests.helpers import service_avec_clients_mockes

        self.servicer = FacturationServicer.__new__(FacturationServicer)
        self.servicer._tarif_svc = TarifService()
        self.servicer._facture_svc = service_avec_clients_mockes()
        self.servicer._campagne_client = MagicMock()
        self.servicer._config_client = MagicMock()
        self.servicer._config_client.get_delai_paiement_jours.return_value = 5
        self.servicer._config_client.get_infos_societe.return_value = InfosSociete(nom="SGFE")

        TarifService().update_tarif(Decimal("500.00"), datetime.date(2025, 7, 1))

        self.servicer._campagne_client.list_releves.return_value = [
            {
                "abonne_id": "abo-001",
                "ancien_index": 100.0,
                "nouveau_index": 115.0,
                "consommation": 15.0,
                "date_releve": "2025-07-15",
                "statut": "RELEVE",
            }
        ]

        import sys
        from pathlib import Path

        from django.conf import settings

        sys.path.insert(0, str(Path(settings.BASE_DIR) / "proto"))
        import facturation_service_pb2 as pb

        self._pb = pb

        with tempfile.TemporaryDirectory() as tmpdir:
            with patch("factures.services.settings") as mock_settings:
                mock_settings.PDF_STORAGE_DIR = tmpdir
                response = self.servicer.GenererFactures(
                    pb.GenererFacturesRequest(campagne_id="camp-setup"), MagicMock()
                )
        self.facture_id = response.factures[0].facture_id

    def test_update_statut_vers_partielle(self):
        response = self.servicer.UpdateStatutFacture(
            self._pb.UpdateStatutRequest(facture_id=self.facture_id, statut=StatutFacture.PARTIELLE),
            MagicMock(),
        )
        self.assertEqual(response.statut, StatutFacture.PARTIELLE)

    def test_update_statut_invalide_propage_validation_error(self):
        with self.assertRaises(ValidationError):
            self.servicer.UpdateStatutFacture(
                self._pb.UpdateStatutRequest(facture_id=self.facture_id, statut="INVALIDE"),
                _make_context(),
            )

    def test_get_facture_introuvable_propage_not_found(self):
        with self.assertRaises(ObjectDoesNotExist):
            self.servicer.GetFacture(
                self._pb.FactureIdRequest(facture_id="00000000-0000-0000-0000-000000000000"),
                _make_context(),
            )
