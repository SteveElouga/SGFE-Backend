import sys
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import MagicMock, patch

from django.conf import settings
from django.test import TestCase

sys.path.insert(0, str(Path(settings.BASE_DIR) / "proto"))

import config_service_pb2 as pb

from parametres.grpc_server import ConfigServiceServicer
from parametres.models import InfosSociete


def _fake_redis_module(client: MagicMock) -> SimpleNamespace:
    return SimpleNamespace(Redis=SimpleNamespace(from_url=MagicMock(return_value=client)))


def _redis_store_backed_client() -> tuple[MagicMock, dict[str, str]]:
    """Client Redis simulé, adossé à un dict Python — sert de vrai cache pour le test."""
    store: dict[str, str] = {}
    client = MagicMock()
    client.setex.side_effect = lambda key, ttl, value: store.__setitem__(key, value)
    client.get.side_effect = lambda key: store.get(key)
    client.delete.side_effect = lambda key: store.pop(key, None)
    return client, store


class ConfigServiceServicerTests(TestCase):
    def setUp(self) -> None:
        self.servicer = ConfigServiceServicer()
        self.context = MagicMock()

    # --- InfosSociete ---

    def test_get_infos_societe_returns_empty_by_default(self) -> None:
        response = self.servicer.GetInfosSociete(pb.EmptyRequest(), self.context)
        self.assertEqual(response.nom, "")

    def test_get_infos_societe_returns_existing(self) -> None:
        InfosSociete.objects.create(pk=1, nom="Eau SA", adresse="Yaoundé", telephone="+237")
        response = self.servicer.GetInfosSociete(pb.EmptyRequest(), self.context)
        self.assertEqual(response.nom, "Eau SA")
        self.assertEqual(response.adresse, "Yaoundé")

    def test_update_infos_societe_updates_fields(self) -> None:
        request = pb.UpdateInfosRequest(nom="Nouvelle Société", adresse="Douala", telephone="+237699000000")
        response = self.servicer.UpdateInfosSociete(request, self.context)
        self.assertEqual(response.nom, "Nouvelle Société")
        self.assertEqual(response.adresse, "Douala")

    def test_update_infos_societe_persists(self) -> None:
        self.servicer.UpdateInfosSociete(pb.UpdateInfosRequest(nom="SGFE"), self.context)
        infos = InfosSociete.objects.get(pk=1)
        self.assertEqual(infos.nom, "SGFE")

    # --- ConfigParam ---

    def test_get_config_returns_default(self) -> None:
        response = self.servicer.GetConfig(pb.ConfigKeyRequest(cle="delai_paiement_jours"), self.context)
        self.assertEqual(response.cle, "delai_paiement_jours")
        self.assertEqual(response.valeur, "5")

    def test_get_config_unknown_key_raises(self) -> None:
        from django.core.exceptions import ObjectDoesNotExist

        with self.assertRaises(ObjectDoesNotExist):
            self.servicer.GetConfig(pb.ConfigKeyRequest(cle="CLE_INCONNUE"), self.context)

    def test_update_config_changes_value(self) -> None:
        response = self.servicer.UpdateConfig(
            pb.UpdateConfigRequest(cle="delai_paiement_jours", valeur="10"),
            self.context,
        )
        self.assertEqual(response.valeur, "10")

    def test_update_config_unknown_key_raises(self) -> None:
        from django.core.exceptions import ObjectDoesNotExist

        with self.assertRaises(ObjectDoesNotExist):
            self.servicer.UpdateConfig(pb.UpdateConfigRequest(cle="CLE_INCONNUE", valeur="42"), self.context)

    def test_list_configs_returns_all_defaults(self) -> None:
        response = self.servicer.ListConfigs(pb.EmptyRequest(), self.context)
        cles = {c.cle for c in response.configs}
        self.assertIn("delai_paiement_jours", cles)
        self.assertIn("token_validite_jours", cles)
        self.assertIn("impaye_suspension_auto", cles)
        self.assertIn("impaye_delai_rappel_1", cles)
        self.assertIn("impaye_delai_suspension", cles)

    def test_list_configs_count_matches_defaults(self) -> None:
        from parametres.models import CONFIG_DEFAULTS

        response = self.servicer.ListConfigs(pb.EmptyRequest(), self.context)
        self.assertEqual(len(response.configs), len(CONFIG_DEFAULTS))

    # --- Cache Redis (GetConfig / GetInfosSociete) ---

    def test_get_config_sert_le_cache_sans_retourner_en_base(self) -> None:
        """Une modification en base après le premier appel ne doit pas être vue
        tant que le cache n'est pas invalidé — c'est bien lui qui sert la 2e lecture."""
        client, _ = _redis_store_backed_client()
        with patch.dict(sys.modules, {"redis": _fake_redis_module(client)}):
            premiere = self.servicer.GetConfig(pb.ConfigKeyRequest(cle="delai_paiement_jours"), self.context)
            self.assertEqual(premiere.valeur, "5")

            from parametres.models import ConfigParam

            ConfigParam.objects.filter(cle="delai_paiement_jours").update(valeur="99")

            seconde = self.servicer.GetConfig(pb.ConfigKeyRequest(cle="delai_paiement_jours"), self.context)
        self.assertEqual(seconde.valeur, "5")  # servie par le cache, pas par la base modifiée

    def test_update_config_invalide_le_cache(self) -> None:
        """Après UpdateConfig, un GetConfig qui suit ne doit JAMAIS resservir
        l'ancienne valeur mise en cache — invalidation explicite, jamais de
        valeur strictement obsolète après une modification volontaire."""
        client, _ = _redis_store_backed_client()
        with patch.dict(sys.modules, {"redis": _fake_redis_module(client)}):
            self.servicer.GetConfig(pb.ConfigKeyRequest(cle="delai_paiement_jours"), self.context)
            self.servicer.UpdateConfig(pb.UpdateConfigRequest(cle="delai_paiement_jours", valeur="10"), self.context)
            apres = self.servicer.GetConfig(pb.ConfigKeyRequest(cle="delai_paiement_jours"), self.context)
        self.assertEqual(apres.valeur, "10")

    def test_get_infos_societe_sert_le_cache_sans_retourner_en_base(self) -> None:
        client, _ = _redis_store_backed_client()
        InfosSociete.objects.create(pk=1, nom="Eau SA", adresse="Yaoundé", telephone="+237")
        with patch.dict(sys.modules, {"redis": _fake_redis_module(client)}):
            premiere = self.servicer.GetInfosSociete(pb.EmptyRequest(), self.context)
            self.assertEqual(premiere.nom, "Eau SA")

            InfosSociete.objects.filter(pk=1).update(nom="Autre Nom")

            seconde = self.servicer.GetInfosSociete(pb.EmptyRequest(), self.context)
        self.assertEqual(seconde.nom, "Eau SA")  # servie par le cache

    def test_update_infos_societe_invalide_le_cache(self) -> None:
        client, _ = _redis_store_backed_client()
        with patch.dict(sys.modules, {"redis": _fake_redis_module(client)}):
            self.servicer.GetInfosSociete(pb.EmptyRequest(), self.context)
            self.servicer.UpdateInfosSociete(pb.UpdateInfosRequest(nom="Nouvelle Société"), self.context)
            apres = self.servicer.GetInfosSociete(pb.EmptyRequest(), self.context)
        self.assertEqual(apres.nom, "Nouvelle Société")
