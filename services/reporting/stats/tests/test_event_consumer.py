"""Tests du consumer d'événements reporting — dispatch + idempotence (apply_event)."""

from decimal import Decimal

from django.test import TestCase

from stats.event_consumer import apply_event
from stats.models import ProcessedEvent, StatsCampagne, StatsFacturation, StatsPaiements
from stats.services import AgregateurDashboard

CAMP = "11111111-1111-1111-1111-111111111111"


class ApplyEventTests(TestCase):
    def setUp(self) -> None:
        self.agg = AgregateurDashboard()

    def test_campagne_stats_set(self) -> None:
        apply_event(
            self.agg,
            {
                "event_id": "c1",
                "type": "CAMPAGNE_STATS",
                "campagne_id": CAMP,
                "nom_campagne": "Juin",
                "total_abonnes": 10,
                "nb_releves": 4,
                "consommation_totale": 120.0,
            },
        )
        stats = StatsCampagne.objects.get(campagne_id=CAMP)
        self.assertEqual(stats.total_abonnes, 10)
        self.assertEqual(stats.nb_releves, 4)
        self.assertEqual(stats.nb_en_attente, 6)

    def test_facturation_stats_increment(self) -> None:
        evt = {
            "event_id": "f1",
            "type": "FACTURATION_STATS",
            "campagne_id": CAMP,
            "delta_factures": 3,
            "delta_montant": 30000.0,
            "type_update": "GENEREE",
        }
        apply_event(self.agg, evt)
        stats = StatsFacturation.objects.get(campagne_id=CAMP)
        self.assertEqual(stats.total_factures, 3)
        self.assertEqual(stats.montant_total_facture, Decimal("30000.00"))

    def test_paiement_stats_increment(self) -> None:
        apply_event(
            self.agg,
            {
                "event_id": "p1",
                "type": "PAIEMENT_STATS",
                "campagne_id": CAMP,
                "montant_paiement": 5000.0,
                "type_update": "PAIEMENT",
            },
        )
        stats = StatsPaiements.objects.get(campagne_id=CAMP)
        self.assertEqual(stats.montant_encaisse, Decimal("5000.00"))

    def test_idempotence_rejeu_meme_event_id(self) -> None:
        """Un même event_id rejoué (at-least-once) ne double PAS un compteur."""
        evt = {
            "event_id": "p-dup",
            "type": "PAIEMENT_STATS",
            "campagne_id": CAMP,
            "montant_paiement": 5000.0,
            "type_update": "PAIEMENT",
        }
        apply_event(self.agg, evt)
        apply_event(self.agg, evt)  # rejeu
        apply_event(self.agg, evt)  # rejeu
        stats = StatsPaiements.objects.get(campagne_id=CAMP)
        self.assertEqual(stats.montant_encaisse, Decimal("5000.00"))  # une seule fois
        self.assertEqual(ProcessedEvent.objects.filter(event_id="p-dup").count(), 1)

    def test_event_sans_id_ignore(self) -> None:
        apply_event(
            self.agg,
            {"type": "PAIEMENT_STATS", "campagne_id": CAMP, "montant_paiement": 1.0},
        )
        self.assertEqual(ProcessedEvent.objects.count(), 0)
        self.assertFalse(StatsPaiements.objects.filter(campagne_id=CAMP).exists())

    def test_type_inconnu_marque_traite_sans_effet(self) -> None:
        apply_event(self.agg, {"event_id": "x1", "type": "INCONNU", "campagne_id": CAMP})
        self.assertTrue(ProcessedEvent.objects.filter(event_id="x1").exists())
        self.assertFalse(StatsPaiements.objects.filter(campagne_id=CAMP).exists())
