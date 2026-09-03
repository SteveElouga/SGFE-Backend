"""Tests du publisher d'événements Redis de l'Auth Service."""

import json
import sys
from types import SimpleNamespace
from unittest.mock import MagicMock, patch

from django.test import SimpleTestCase

from comptes.event_publisher import CHANNEL, publish_user_event


def _fake_redis_module(client: MagicMock) -> SimpleNamespace:
    return SimpleNamespace(Redis=SimpleNamespace(from_url=MagicMock(return_value=client)))


class PublishUserEventTests(SimpleTestCase):
    def test_publie_le_bon_payload_sur_le_bon_canal(self) -> None:
        client = MagicMock()
        with patch.dict(sys.modules, {"redis": _fake_redis_module(client)}):
            publish_user_event("u-1", "USER_CREATED")

        channel, payload = client.publish.call_args.args
        self.assertEqual(channel, CHANNEL)
        self.assertEqual(json.loads(payload), {"event_type": "USER_CREATED", "user_id": "u-1"})
        client.close.assert_called_once()

    def test_best_effort_sur_echec_redis(self) -> None:
        client = MagicMock()
        client.publish.side_effect = RuntimeError("redis down")
        with patch.dict(sys.modules, {"redis": _fake_redis_module(client)}):
            publish_user_event("u-1")  # ne doit pas lever
