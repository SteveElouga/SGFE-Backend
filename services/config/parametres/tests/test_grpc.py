import sys
from pathlib import Path
from unittest.mock import MagicMock

from django.conf import settings
from django.test import TestCase

sys.path.insert(0, str(Path(settings.BASE_DIR) / "proto"))

import config_service_pb2 as pb

from parametres.grpc_server import ConfigServiceServicer
from parametres.models import InfosSociete


class ConfigServiceServicerTests(TestCase):
    def setUp(self):
        self.servicer = ConfigServiceServicer()
        self.context = MagicMock()

    # --- InfosSociete ---

    def test_get_infos_societe_returns_empty_by_default(self):
        response = self.servicer.GetInfosSociete(pb.EmptyRequest(), self.context)
        self.assertEqual(response.nom, "")

    def test_get_infos_societe_returns_existing(self):
        InfosSociete.objects.create(pk=1, nom="Eau SA", adresse="Yaoundé", telephone="+237")
        response = self.servicer.GetInfosSociete(pb.EmptyRequest(), self.context)
        self.assertEqual(response.nom, "Eau SA")
        self.assertEqual(response.adresse, "Yaoundé")

    def test_update_infos_societe_updates_fields(self):
        request = pb.UpdateInfosRequest(nom="Nouvelle Société", adresse="Douala", telephone="+237699000000")
        response = self.servicer.UpdateInfosSociete(request, self.context)
        self.assertEqual(response.nom, "Nouvelle Société")
        self.assertEqual(response.adresse, "Douala")

    def test_update_infos_societe_persists(self):
        self.servicer.UpdateInfosSociete(pb.UpdateInfosRequest(nom="SGFE"), self.context)
        infos = InfosSociete.objects.get(pk=1)
        self.assertEqual(infos.nom, "SGFE")

    # --- ConfigParam ---

    def test_get_config_returns_default(self):
        response = self.servicer.GetConfig(pb.ConfigKeyRequest(cle="delai_paiement_jours"), self.context)
        self.assertEqual(response.cle, "delai_paiement_jours")
        self.assertEqual(response.valeur, "5")

    def test_get_config_unknown_key_raises(self):
        from django.core.exceptions import ObjectDoesNotExist

        with self.assertRaises(ObjectDoesNotExist):
            self.servicer.GetConfig(pb.ConfigKeyRequest(cle="CLE_INCONNUE"), self.context)

    def test_update_config_changes_value(self):
        response = self.servicer.UpdateConfig(
            pb.UpdateConfigRequest(cle="delai_paiement_jours", valeur="10"),
            self.context,
        )
        self.assertEqual(response.valeur, "10")

    def test_update_config_unknown_key_raises(self):
        from django.core.exceptions import ObjectDoesNotExist

        with self.assertRaises(ObjectDoesNotExist):
            self.servicer.UpdateConfig(pb.UpdateConfigRequest(cle="CLE_INCONNUE", valeur="42"), self.context)

    def test_list_configs_returns_all_defaults(self):
        response = self.servicer.ListConfigs(pb.EmptyRequest(), self.context)
        cles = {c.cle for c in response.configs}
        self.assertIn("delai_paiement_jours", cles)
        self.assertIn("token_validite_jours", cles)
        self.assertIn("impaye_suspension_auto", cles)
        self.assertIn("impaye_delai_rappel_1", cles)
        self.assertIn("impaye_delai_suspension", cles)

    def test_list_configs_count_matches_defaults(self):
        from parametres.models import CONFIG_DEFAULTS

        response = self.servicer.ListConfigs(pb.EmptyRequest(), self.context)
        self.assertEqual(len(response.configs), len(CONFIG_DEFAULTS))
