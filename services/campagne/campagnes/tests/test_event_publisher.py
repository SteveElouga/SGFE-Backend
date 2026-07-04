"""Tests du publisher d'événements Redis du Campagne Service."""

import json
import sys
from types import SimpleNamespace
from unittest.mock import MagicMock, patch

from django.test import SimpleTestCase

from campagnes.event_publisher import CHANNEL, publish_progression_event


def _fake_redis_module(client: MagicMock) -> SimpleNamespace:
    return SimpleNamespace(Redis=SimpleNamespace(from_url=MagicMock(return_value=client)))


class PublishProgressionEventTests(SimpleTestCase):
    def test_publie_le_bon_payload_sur_le_bon_canal(self):
        client = MagicMock()
        with patch.dict(sys.modules, {"redis": _fake_redis_module(client)}):
            publish_progression_event("camp-1")

        channel, payload = client.publish.call_args.args
        self.assertEqual(channel, CHANNEL)
        self.assertEqual(json.loads(payload), {"event_type": "PROGRESSION_UPDATED", "campagne_id": "camp-1"})
        client.close.assert_called_once()

    def test_best_effort_sur_echec_redis(self):
        client = MagicMock()
        client.publish.side_effect = RuntimeError("redis down")
        with patch.dict(sys.modules, {"redis": _fake_redis_module(client)}):
            publish_progression_event("camp-1")  # ne doit pas lever
