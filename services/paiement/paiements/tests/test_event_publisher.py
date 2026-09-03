"""Tests du publisher d'événements Redis du Paiement Service."""

import json
import sys
from datetime import date, datetime
from decimal import Decimal
from types import SimpleNamespace
from unittest.mock import MagicMock, patch

from django.test import SimpleTestCase

from paiements.event_publisher import CHANNEL, publish_paiement_event


def _fake_redis_module(client: MagicMock) -> SimpleNamespace:
    return SimpleNamespace(Redis=SimpleNamespace(from_url=MagicMock(return_value=client)))


def _fake_paiement() -> SimpleNamespace:
    return SimpleNamespace(
        id="pay-1",
        facture_id="fac-1",
        montant=Decimal("5000"),
        date_paiement=date(2026, 7, 4),
        mode_paiement="ESPECES",
        reference_transaction="",
        created_at=datetime(2026, 7, 4, 10, 0, 0),
        enregistre_par="user-1",
    )


class PublishPaiementEventTests(SimpleTestCase):
    def test_payload_auto_porteur_complet(self) -> None:
        client = MagicMock()
        with patch.dict(sys.modules, {"redis": _fake_redis_module(client)}):
            publish_paiement_event(_fake_paiement(), statut_facture="PARTIELLE")

        channel, payload = client.publish.call_args.args
        self.assertEqual(channel, CHANNEL)
        self.assertEqual(
            json.loads(payload),
            {
                "event_type": "PAIEMENT_CREATED",
                "paiement_id": "pay-1",
                "facture_id": "fac-1",
                "montant": 5000.0,
                "date_paiement": "2026-07-04",
                "mode_paiement": "ESPECES",
                "reference_transaction": "",
                "created_at": "2026-07-04T10:00:00",
                "enregistre_par": "user-1",
                "statut_facture": "PARTIELLE",
            },
        )
        client.close.assert_called_once()

    def test_best_effort_sur_echec_redis(self) -> None:
        client = MagicMock()
        client.publish.side_effect = RuntimeError("redis down")
        with patch.dict(sys.modules, {"redis": _fake_redis_module(client)}):
            publish_paiement_event(_fake_paiement(), statut_facture="PAYEE")  # ne doit pas lever
