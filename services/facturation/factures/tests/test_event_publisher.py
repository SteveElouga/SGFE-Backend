"""Tests du publisher d'événements Redis du Facturation Service."""

import json
import sys
from types import SimpleNamespace
from unittest.mock import MagicMock, patch

from django.test import SimpleTestCase

from factures.event_publisher import (
    CHANNEL,
    TARIF_CHANNEL,
    publish_facture_event,
    publish_tarif_event,
)


def _fake_redis_module(client: MagicMock) -> SimpleNamespace:
    """Faux module `redis` dont Redis.from_url renvoie le client fourni."""
    return SimpleNamespace(Redis=SimpleNamespace(from_url=MagicMock(return_value=client)))


class PublishFactureEventTests(SimpleTestCase):
    def test_publie_le_bon_payload_sur_le_bon_canal(self) -> None:
        client = MagicMock()
        with patch.dict(sys.modules, {"redis": _fake_redis_module(client)}):
            publish_facture_event("fac-1", "camp-1", "FACTURE_CREATED")

        channel, payload = client.publish.call_args.args
        self.assertEqual(channel, CHANNEL)
        self.assertEqual(
            json.loads(payload),
            {"event_type": "FACTURE_CREATED", "facture_id": "fac-1", "campagne_id": "camp-1"},
        )
        client.close.assert_called_once()

    def test_best_effort_sur_echec_redis(self) -> None:
        """Un échec Redis ne doit jamais se propager (dégradation gracieuse)."""
        client = MagicMock()
        client.publish.side_effect = RuntimeError("redis down")
        with patch.dict(sys.modules, {"redis": _fake_redis_module(client)}):
            publish_facture_event("fac-1", "camp-1")  # ne doit pas lever


class PublishTarifEventTests(SimpleTestCase):
    def test_publie_sur_le_canal_tarif(self) -> None:
        client = MagicMock()
        with patch.dict(sys.modules, {"redis": _fake_redis_module(client)}):
            publish_tarif_event()

        channel, payload = client.publish.call_args.args
        self.assertEqual(channel, TARIF_CHANNEL)
        self.assertEqual(json.loads(payload), {"event_type": "TARIF_UPDATED"})
        client.close.assert_called_once()

    def test_best_effort_sur_echec_redis(self) -> None:
        client = MagicMock()
        client.publish.side_effect = RuntimeError("redis down")
        with patch.dict(sys.modules, {"redis": _fake_redis_module(client)}):
            publish_tarif_event()  # ne doit pas lever
