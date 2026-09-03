"""Tests du cache Redis court de Config Service (GetConfig / GetInfosSociete)."""

import sys
from types import SimpleNamespace
from unittest.mock import MagicMock, patch

from django.test import SimpleTestCase

from parametres.cache import (
    TTL_SECONDS,
    get_cached_infos_societe,
    get_cached_param,
    invalidate_infos_societe,
    invalidate_param,
    set_cached_infos_societe,
    set_cached_param,
)


def _fake_redis_module(client: MagicMock) -> SimpleNamespace:
    return SimpleNamespace(Redis=SimpleNamespace(from_url=MagicMock(return_value=client)))


class ConfigParamCacheTests(SimpleTestCase):
    def test_set_puis_get_retourne_la_valeur_avec_ttl_court(self) -> None:
        store: dict[str, str] = {}
        client = MagicMock()
        client.setex.side_effect = lambda key, ttl, value: store.__setitem__(key, value)
        client.get.side_effect = lambda key: store.get(key)
        with patch.dict(sys.modules, {"redis": _fake_redis_module(client)}):
            set_cached_param("delai_paiement_jours", {"cle": "delai_paiement_jours", "valeur": "5", "description": ""})
            resultat = get_cached_param("delai_paiement_jours")

        self.assertEqual(resultat, {"cle": "delai_paiement_jours", "valeur": "5", "description": ""})
        ttl_utilise = client.setex.call_args.args[1]
        self.assertEqual(ttl_utilise, TTL_SECONDS)

    def test_get_sans_valeur_en_cache_retourne_none(self) -> None:
        client = MagicMock()
        client.get.return_value = None
        with patch.dict(sys.modules, {"redis": _fake_redis_module(client)}):
            self.assertIsNone(get_cached_param("cle_absente"))

    def test_invalidate_supprime_la_cle(self) -> None:
        client = MagicMock()
        with patch.dict(sys.modules, {"redis": _fake_redis_module(client)}):
            invalidate_param("delai_paiement_jours")
        cle_supprimee = client.delete.call_args.args[0]
        self.assertIn("delai_paiement_jours", cle_supprimee)

    def test_lecture_best_effort_sur_echec_redis(self) -> None:
        client = MagicMock()
        client.get.side_effect = RuntimeError("redis down")
        with patch.dict(sys.modules, {"redis": _fake_redis_module(client)}):
            self.assertIsNone(get_cached_param("delai_paiement_jours"))  # ne doit pas lever

    def test_ecriture_best_effort_sur_echec_redis(self) -> None:
        client = MagicMock()
        client.setex.side_effect = RuntimeError("redis down")
        with patch.dict(sys.modules, {"redis": _fake_redis_module(client)}):
            set_cached_param(
                "delai_paiement_jours", {"cle": "delai_paiement_jours", "valeur": "5"}
            )  # ne doit pas lever

    def test_invalidation_best_effort_sur_echec_redis(self) -> None:
        client = MagicMock()
        client.delete.side_effect = RuntimeError("redis down")
        with patch.dict(sys.modules, {"redis": _fake_redis_module(client)}):
            invalidate_param("delai_paiement_jours")  # ne doit pas lever

    def test_module_redis_absent_degrade_gracieusement(self) -> None:
        with patch.dict(sys.modules, {"redis": None}):
            self.assertIsNone(get_cached_param("delai_paiement_jours"))
            set_cached_param("delai_paiement_jours", {"cle": "x"})  # ne doit pas lever
            invalidate_param("delai_paiement_jours")  # ne doit pas lever


class InfosSocieteCacheTests(SimpleTestCase):
    def test_set_puis_get_retourne_la_valeur(self) -> None:
        store: dict[str, str] = {}
        client = MagicMock()
        client.setex.side_effect = lambda key, ttl, value: store.__setitem__(key, value)
        client.get.side_effect = lambda key: store.get(key)
        data = {"nom": "Eau SA", "adresse": "Yaoundé", "telephone": "+237", "logo_path": "", "updated_at": ""}
        with patch.dict(sys.modules, {"redis": _fake_redis_module(client)}):
            set_cached_infos_societe(data)
            resultat = get_cached_infos_societe()

        self.assertEqual(resultat, data)

    def test_invalidate_supprime_la_cle_infos_societe(self) -> None:
        client = MagicMock()
        with patch.dict(sys.modules, {"redis": _fake_redis_module(client)}):
            invalidate_infos_societe()
        client.delete.assert_called_once()

    def test_lecture_best_effort_sur_echec_redis(self) -> None:
        client = MagicMock()
        client.get.side_effect = RuntimeError("redis down")
        with patch.dict(sys.modules, {"redis": _fake_redis_module(client)}):
            self.assertIsNone(get_cached_infos_societe())  # ne doit pas lever
