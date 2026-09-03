"""Tests du publisher d'événements Redis du Config Service."""

import json
import sys
from types import SimpleNamespace
from unittest.mock import MagicMock, patch

from django.test import SimpleTestCase

from parametres.event_publisher import CHANNEL, publish_config_event


def _fake_redis_module(client: MagicMock) -> SimpleNamespace:
    return SimpleNamespace(Redis=SimpleNamespace(from_url=MagicMock(return_value=client)))


class PublishConfigEventTests(SimpleTestCase):
    def test_publie_le_bon_payload_sur_le_bon_canal(self) -> None:
        client = MagicMock()
        with patch.dict(sys.modules, {"redis": _fake_redis_module(client)}):
            publish_config_event("delai_paiement_jours", "CONFIG_UPDATED")

        channel, payload = client.publish.call_args.args
        self.assertEqual(channel, CHANNEL)
        self.assertEqual(json.loads(payload), {"event_type": "CONFIG_UPDATED", "cle": "delai_paiement_jours"})
        client.close.assert_called_once()

    def test_best_effort_sur_echec_redis(self) -> None:
        client = MagicMock()
        client.publish.side_effect = RuntimeError("redis down")
        with patch.dict(sys.modules, {"redis": _fake_redis_module(client)}):
            publish_config_event("x")  # ne doit pas lever
