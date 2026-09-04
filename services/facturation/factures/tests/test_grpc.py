"""Tests du serveur gRPC du Facturation Service."""

import datetime
import tempfile
from decimal import Decimal
from typing import Any
from unittest.mock import MagicMock, patch

import grpc
from django.core.exceptions import ObjectDoesNotExist, ValidationError
from django.test import TestCase

from factures.exceptions import PreconditionError
from factures.models import Facture, StatutFacture, Tarif
from factures.pdf_generator import InfosSociete
from factures.services import TarifService


def _make_context() -> MagicMock:
    # Le mapping exception -> abort est fait par l'interceptor (testé dans
    # test_grpc_interceptors.py) : le servicer propage l'exception métier.
    return MagicMock()


class GetTarifActuelTests(TestCase):
    def setUp(self) -> None:
        from factures.grpc_server import FacturationServicer

        self.servicer = FacturationServicer.__new__(FacturationServicer)
        self.servicer._tarif_svc = TarifService()
        self.servicer._facture_svc = MagicMock()
        self.servicer._campagne_client = MagicMock()
        self.servicer._config_client = MagicMock()

    def _pb(self) -> Any:
        # `Any` : module de stubs gRPC générés (facturation_service_pb2), exclu
        # de la vérification mypy (voir mypy.ini) — rien à typer de plus précis.
        import sys
        from pathlib import Path

        from django.conf import settings

        sys.path.insert(0, str(Path(settings.BASE_DIR) / "proto"))
        import facturation_service_pb2 as pb

        return pb

    def test_get_tarif_actuel_succes(self) -> None:
        TarifService().update_tarif(Decimal("500.00"), datetime.date(2025, 7, 1))
        pb = self._pb()
        response = self.servicer.GetTarifActuel(pb.EmptyRequest(), MagicMock())
        self.assertAlmostEqual(response.prix_m3, 500.0)
        self.assertTrue(response.is_active)

    def test_get_tarif_actuel_absent_propage_not_found(self) -> None:
        Tarif.objects.all().delete()
        pb = self._pb()
        with self.assertRaises(ObjectDoesNotExist):
            self.servicer.GetTarifActuel(pb.EmptyRequest(), _make_context())

    def test_update_tarif_succes(self) -> None:
        pb = self._pb()
        request = pb.UpdateTarifRequest(prix_m3=600.0, date_effet="2025-08-01")
        response = self.servicer.UpdateTarif(request, MagicMock())
        self.assertAlmostEqual(response.prix_m3, 600.0)
        self.assertTrue(response.is_active)

    def test_update_tarif_prix_invalide_propage_validation_error(self) -> None:
        pb = self._pb()
        request = pb.UpdateTarifRequest(prix_m3=0.0, date_effet="2025-08-01")
        with self.assertRaises(ValidationError):
            self.servicer.UpdateTarif(request, _make_context())


class GenererFacturesTests(TestCase):
    def setUp(self) -> None:
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

    def _pb(self) -> Any:
        # `Any` : module de stubs gRPC générés (facturation_service_pb2), exclu
        # de la vérification mypy (voir mypy.ini) — rien à typer de plus précis.
        import sys
        from pathlib import Path

        from django.conf import settings

        sys.path.insert(0, str(Path(settings.BASE_DIR) / "proto"))
        import facturation_service_pb2 as pb

        return pb

    def test_generer_factures_succes(self) -> None:
        self.servicer._campagne_client.list_releves.return_value = [  # type: ignore[attr-defined]
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

    def test_generer_factures_campagne_service_ko_propage_rpc_error(self) -> None:
        self.servicer._campagne_client.list_releves.side_effect = grpc.RpcError()  # type: ignore[attr-defined]
        pb = self._pb()
        with self.assertRaises(grpc.RpcError):
            self.servicer.GenererFactures(pb.GenererFacturesRequest(campagne_id="camp-002"), _make_context())

    def test_generer_factures_sans_tarif_propage_precondition_error(self) -> None:
        Tarif.objects.all().delete()
        self.servicer._campagne_client.list_releves.return_value = [  # type: ignore[attr-defined]
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
    def setUp(self) -> None:
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

    def test_update_statut_vers_partielle(self) -> None:
        response = self.servicer.UpdateStatutFacture(
            self._pb.UpdateStatutRequest(facture_id=self.facture_id, statut=StatutFacture.PARTIELLE),
            MagicMock(),
        )
        self.assertEqual(response.statut, StatutFacture.PARTIELLE)

    def test_update_statut_invalide_propage_validation_error(self) -> None:
        with self.assertRaises(ValidationError):
            self.servicer.UpdateStatutFacture(
                self._pb.UpdateStatutRequest(facture_id=self.facture_id, statut="INVALIDE"),
                _make_context(),
            )

    def test_get_facture_introuvable_propage_not_found(self) -> None:
        with self.assertRaises(ObjectDoesNotExist):
            self.servicer.GetFacture(
                self._pb.FactureIdRequest(facture_id="00000000-0000-0000-0000-000000000000"),
                _make_context(),
            )


class AnnulerFactureTests(TestCase):
    """`AnnulerFacture` (le RPC, pas le service) doit notifier la gateway
    comme le fait déjà `UpdateStatutFacture` — sinon un écran de facture
    ouvert au moment de l'annulation ne le voit qu'au prochain rechargement."""

    def setUp(self) -> None:
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
                    pb.GenererFacturesRequest(campagne_id="camp-annulation"), MagicMock()
                )
        self.facture_id = response.factures[0].facture_id
        self.campagne_id = response.factures[0].campagne_id

    @patch("factures.grpc_server.publish_facture_event")
    def test_annuler_facture_notifie_la_gateway(self, mock_publish: MagicMock) -> None:
        response = self.servicer.AnnulerFacture(
            self._pb.AnnulerFactureRequest(facture_id=self.facture_id, motif="erreur d'index", annule_par="admin-1"),
            _make_context(),
        )

        self.assertEqual(response.statut, StatutFacture.ANNULEE)
        mock_publish.assert_called_once_with(self.facture_id, self.campagne_id, "FACTURE_UPDATED")


class ListFacturesTests(TestCase):
    """`limit`/`offset` optionnels sur `ListFactures` — rétrocompatibilité
    stricte (omis, comportement historique inchangé), combinaison avec les
    filtres existants, et `total` cohérent avec le nombre réel de lignes
    filtrées (pas la page rendue)."""

    def setUp(self) -> None:
        from factures.grpc_server import FacturationServicer
        from factures.tests.helpers import service_avec_clients_mockes

        self.servicer = FacturationServicer.__new__(FacturationServicer)
        self.servicer._facture_svc = service_avec_clients_mockes()

        for i in range(5):
            f = Facture.objects.create(
                numero_facture=f"FACT-{i}",
                abonne_id="ab-1",
                campagne_id="camp-x",
                ancien_index=Decimal("0"),
                nouveau_index=Decimal("10"),
                consommation=Decimal("10"),
                prix_m3=Decimal("500"),
                montant=Decimal("5000"),
                statut=StatutFacture.IMPAYEE,
                date_releve=datetime.date(2026, 7, 1 + i),
                date_limite_paiement=datetime.date(2026, 7, 6 + i),
            )
            # `date_generation` est `auto_now_add` : repositionnée pour un tri
            # déterministe (le repository trie par `-date_generation`).
            Facture.objects.filter(pk=f.pk).update(
                date_generation=datetime.datetime(2026, 7, 1 + i, 9, 0, tzinfo=datetime.UTC)
            )

    def _pb(self) -> Any:
        # `Any` : module de stubs gRPC générés (facturation_service_pb2), exclu
        # de la vérification mypy (voir mypy.ini) — rien à typer de plus précis.
        import sys
        from pathlib import Path

        from django.conf import settings

        sys.path.insert(0, str(Path(settings.BASE_DIR) / "proto"))
        import facturation_service_pb2 as pb

        return pb

    def test_sans_pagination_renvoie_tout_et_total_coherent(self) -> None:
        # Non-régression : `limit`/`offset` omis (champs proto3 `optional`
        # non définis) doit préserver le comportement historique.
        pb = self._pb()
        response = self.servicer.ListFactures(pb.ListFacturesRequest(campagne_id="camp-x"), _make_context())
        self.assertEqual(len(response.factures), 5)
        self.assertEqual(response.total, 5)

    def test_avec_pagination_tronque_et_ordonne_du_plus_recent(self) -> None:
        pb = self._pb()
        response = self.servicer.ListFactures(
            pb.ListFacturesRequest(campagne_id="camp-x", limit=2, offset=0), _make_context()
        )
        self.assertEqual([f.numero_facture for f in response.factures], ["FACT-4", "FACT-3"])
        # Le total porte sur l'ensemble filtré, pas sur la seule page rendue.
        self.assertEqual(response.total, 5)

    def test_pagination_hors_limites_renvoie_liste_vide_pas_une_erreur(self) -> None:
        pb = self._pb()
        response = self.servicer.ListFactures(
            pb.ListFacturesRequest(campagne_id="camp-x", limit=10, offset=100), _make_context()
        )
        self.assertEqual(len(response.factures), 0)
        self.assertEqual(response.total, 5)

    def test_pagination_se_combine_au_filtre_statut(self) -> None:
        # La pagination doit porter sur le résultat FILTRÉ par statut, pas sur
        # la table brute.
        pb = self._pb()
        f0 = Facture.objects.get(numero_facture="FACT-0")
        f0.statut = StatutFacture.PARTIELLE
        f0.save(update_fields=["statut"])

        response = self.servicer.ListFactures(
            pb.ListFacturesRequest(campagne_id="camp-x", statut="IMPAYEE", limit=2, offset=0), _make_context()
        )
        self.assertEqual(len(response.factures), 2)


class UpdateTarifRevalidationRoleTests(TestCase):
    """Défense en profondeur (voir docs/CONFORMITE_SOC2_OWASP.md §3.1 A01,
    plan de remédiation item #3) : `UpdateTarif` revalide le rôle de
    l'appelant à partir de l'identité propagée par la gateway
    (`get_caller()`), en plus du RBAC déjà appliqué côté gateway
    (`gateway/schema/facturation_mutations.py`, `require_role(info, "ADMIN")`
    sur `update_tarif`).

    Compromis assumé (documenté sur `_revalider_role_tarif`) : ce filet ne
    bloque JAMAIS l'appel, même avec un mauvais rôle ou une identité
    absente — il se contente de journaliser un avertissement.
    """

    def setUp(self) -> None:
        from factures.grpc_server import FacturationServicer

        self.servicer = FacturationServicer.__new__(FacturationServicer)
        self.servicer._tarif_svc = TarifService()
        self.servicer._facture_svc = MagicMock()
        self.servicer._campagne_client = MagicMock()
        self.servicer._config_client = MagicMock()

    def _pb(self) -> Any:
        import sys
        from pathlib import Path

        from django.conf import settings

        sys.path.insert(0, str(Path(settings.BASE_DIR) / "proto"))
        import facturation_service_pb2 as pb

        return pb

    def _poser_identite(self, role: str) -> None:
        from factures.grpc_interceptors import CallerIdentity, caller_identity

        jeton = caller_identity.set(CallerIdentity(user_id="u-1", username="testeur", role=role))
        self.addCleanup(caller_identity.reset, jeton)

    @patch("factures.grpc_server.logger")
    def test_role_admin_passe_sans_avertissement_de_role(self, mock_logger: MagicMock) -> None:
        self._poser_identite("ADMIN")
        pb = self._pb()
        request = pb.UpdateTarifRequest(prix_m3=650.0, date_effet="2025-09-01")
        response = self.servicer.UpdateTarif(request, MagicMock())
        self.assertAlmostEqual(response.prix_m3, 650.0)
        for appel in mock_logger.warning.call_args_list:
            self.assertNotIn("hors de l'ensemble autorisé", appel.args[0])

    def test_role_non_autorise_journalise_un_avertissement_mais_passe(self) -> None:
        self._poser_identite("COMPTABLE")
        pb = self._pb()
        request = pb.UpdateTarifRequest(prix_m3=650.0, date_effet="2025-09-01")
        with self.assertLogs("factures.grpc_server", level="WARNING") as journaux:
            response = self.servicer.UpdateTarif(request, MagicMock())
        self.assertAlmostEqual(response.prix_m3, 650.0)  # jamais bloqué (voir docstring de la classe)
        trace = "\n".join(journaux.output)
        self.assertIn("UpdateTarif", trace)
        self.assertIn("hors de l'ensemble autorisé", trace)
        self.assertIn("COMPTABLE", trace)

    def test_sans_identite_reste_retrocompatible(self) -> None:
        """Aucune identité propagée (appel hors gateway, ou service-à-service
        légitime) : comportement inchangé — aucune exception."""
        pb = self._pb()
        request = pb.UpdateTarifRequest(prix_m3=650.0, date_effet="2025-09-01")
        response = self.servicer.UpdateTarif(request, MagicMock())
        self.assertAlmostEqual(response.prix_m3, 650.0)
