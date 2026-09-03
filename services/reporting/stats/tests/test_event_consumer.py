"""Tests du consumer d'événements reporting — dispatch + idempotence (apply_event)
et de la boucle de redélivraison bornée + dead-letter (_handle_entries).
"""

import json
from decimal import Decimal
from typing import Any

from django.test import TestCase

from stats.event_consumer import (
    CONSUMER_NAME,
    DEAD_LETTER_STREAM,
    GROUP,
    MAX_DELIVERY_ATTEMPTS,
    STREAM_KEY,
    _handle_entries,
    apply_event,
)
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

    def test_facturation_stats_annulee_transmet_etait_payee(self) -> None:
        apply_event(
            self.agg,
            {
                "event_id": "f2",
                "type": "FACTURATION_STATS",
                "campagne_id": CAMP,
                "delta_factures": 5,
                "delta_montant": 50000.0,
                "type_update": "GENEREE",
            },
        )
        apply_event(
            self.agg,
            {
                "event_id": "f3",
                "type": "FACTURATION_STATS",
                "campagne_id": CAMP,
                "delta_factures": 2,
                "delta_montant": 0,
                "type_update": "PAYEE",
            },
        )
        apply_event(
            self.agg,
            {
                "event_id": "f4",
                "type": "FACTURATION_STATS",
                "campagne_id": CAMP,
                "delta_factures": 1,
                "delta_montant": 10000.0,
                "type_update": "ANNULEE",
                "etait_payee": True,
            },
        )
        stats = StatsFacturation.objects.get(campagne_id=CAMP)
        self.assertEqual(stats.total_factures, 4)
        self.assertEqual(stats.nb_factures_payees, 1)

    def test_facturation_stats_annulee_sans_etait_payee_suppose_impayee(self) -> None:
        """Absent du payload (ancien producteur), le repli est « impayée » —
        le sens le plus fréquent, jamais un compteur qui devient négatif."""
        apply_event(
            self.agg,
            {
                "event_id": "f5",
                "type": "FACTURATION_STATS",
                "campagne_id": CAMP,
                "delta_factures": 5,
                "delta_montant": 50000.0,
                "type_update": "GENEREE",
            },
        )
        apply_event(
            self.agg,
            {
                "event_id": "f6",
                "type": "FACTURATION_STATS",
                "campagne_id": CAMP,
                "delta_factures": 1,
                "delta_montant": 10000.0,
                "type_update": "ANNULEE",
            },
        )
        stats = StatsFacturation.objects.get(campagne_id=CAMP)
        self.assertEqual(stats.nb_factures_impayees, 4)

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


class _FakeRedis:
    """Double minimal du client redis-py — seulement ce dont `_handle_entries`
    a besoin (xack, xadd, xpending_range). `_handle_entries` reçoit déjà son
    client Redis en paramètre : l'injection suffit, pas besoin d'un vrai Redis
    ni de `fakeredis` (absent de requirements.txt).

    `delivery_counts` simule ce que Redis renvoie via XPENDING pour
    `times_delivered` — le compteur qui, dans l'incident réel, montait à 12-16
    parce que rien ne le plafonnait.
    """

    def __init__(self, delivery_counts: dict[str, int]) -> None:
        self._delivery_counts = delivery_counts
        self.acked: list[str] = []
        self.dead_lettered: list[dict[str, Any]] = []
        self.xpending_calls = 0

    def xack(self, stream: str, group: str, msg_id: str) -> None:
        assert stream == STREAM_KEY
        assert group == GROUP
        self.acked.append(msg_id)

    def xadd(self, stream: str, fields: dict[str, Any]) -> None:
        assert stream == DEAD_LETTER_STREAM
        self.dead_lettered.append(fields)

    def xpending_range(self, stream: str, group: str, min_id: str, max_id: str, count: int) -> list[dict[str, Any]]:
        assert stream == STREAM_KEY
        assert group == GROUP
        self.xpending_calls += 1
        times = self._delivery_counts.get(min_id, 1)
        return [
            {
                "message_id": min_id,
                "consumer": CONSUMER_NAME,
                "time_since_delivered": 0,
                "times_delivered": times,
            }
        ]


def _evenement_paiement_campagne_vide(event_id: str) -> dict[str, Any]:
    """Reproduit exactement la donnée invalide observée dans l'incident réel :
    un événement PAIEMENT_STATS avec `campagne_id=""`, qui casse le champ
    UUID de StatsPaiements (`django.core.exceptions.ValidationError:
    '"" n'est pas un UUID valide'`, confirmé dans les logs de reporting-service)."""
    return {
        "data": json.dumps(
            {
                "event_id": event_id,
                "type": "PAIEMENT_STATS",
                "campagne_id": "",
                "montant_paiement": 10.0,
                "type_update": "PAIEMENT",
            }
        )
    }


class HandleEntriesRedeliveryTests(TestCase):
    """Reproduit le scénario de l'incident (redélivraison bloquée) et vérifie
    le correctif : compteur de tentatives borné (MAX_DELIVERY_ATTEMPTS) puis
    dead-letter, plutôt qu'une boucle infinie de redélivraison sans XACK."""

    def setUp(self) -> None:
        self.agg = AgregateurDashboard()

    def test_evenement_qui_echoue_reste_pending_sous_le_seuil(self) -> None:
        """En dessous du seuil, on se comporte comme avant : pas de XACK, pas
        de dead-letter — l'entrée reste pending pour être redélivrée (utile
        pour absorber une panne transitoire, ex. Postgres injoignable)."""
        fields = _evenement_paiement_campagne_vide("incident-2a")
        entries = [(STREAM_KEY, [("2-0", fields)])]
        fake = _FakeRedis({"2-0": 1})

        _handle_entries(fake, self.agg, entries)

        self.assertEqual(fake.acked, [])
        self.assertEqual(fake.dead_lettered, [])
        self.assertEqual(ProcessedEvent.objects.filter(event_id="incident-2a").count(), 0)

    def test_evenement_qui_echoue_systematiquement_est_dead_lettre_au_seuil(self) -> None:
        """Au seuil MAX_DELIVERY_ATTEMPTS, on renonce : XACK (l'entrée sort du
        PEL, elle ne sera plus jamais redélivrée) + XADD vers le flux
        dead-letter avec l'id d'origine, la donnée brute et l'erreur."""
        fields = _evenement_paiement_campagne_vide("incident-2b")
        entries = [(STREAM_KEY, [("3-0", fields)])]
        fake = _FakeRedis({"3-0": MAX_DELIVERY_ATTEMPTS})

        _handle_entries(fake, self.agg, entries)

        self.assertEqual(fake.acked, ["3-0"])
        self.assertEqual(len(fake.dead_lettered), 1)
        dead = fake.dead_lettered[0]
        self.assertEqual(dead["original_id"], "3-0")
        self.assertEqual(dead["data"], fields["data"])
        self.assertIn("ValidationError", dead["error"])
        # Jamais marqué "traité" : la transaction a été annulée avec l'échec.
        self.assertEqual(ProcessedEvent.objects.filter(event_id="incident-2b").count(), 0)

    def test_incident_redelivrance_bloquee_resolue_par_dead_letter(self) -> None:
        """Bout en bout : simule les redémarrages successifs du consumer qui,
        avant le correctif, rejouaient indéfiniment la même entrée pending
        (191 événements bloqués observés en prod, `times_delivered` jusqu'à
        16). Chaque itération ici correspond à un redémarrage — le compteur
        vient de Redis (XPENDING), pas d'un état en mémoire qui repartirait
        de zéro à chaque process."""
        fields = _evenement_paiement_campagne_vide("incident-3")
        entries = [(STREAM_KEY, [("42-0", fields)])]

        for attempt in range(1, MAX_DELIVERY_ATTEMPTS):
            fake = _FakeRedis({"42-0": attempt})
            _handle_entries(fake, self.agg, entries)
            self.assertEqual(fake.acked, [], f"ne doit pas être acquitté à la tentative {attempt}")
            self.assertEqual(fake.dead_lettered, [])

        # Tentative MAX_DELIVERY_ATTEMPTS : on cesse de redélivrer.
        fake_final = _FakeRedis({"42-0": MAX_DELIVERY_ATTEMPTS})
        _handle_entries(fake_final, self.agg, entries)

        self.assertEqual(fake_final.acked, ["42-0"])
        self.assertEqual(len(fake_final.dead_lettered), 1)
        self.assertEqual(fake_final.dead_lettered[0]["original_id"], "42-0")
        self.assertEqual(ProcessedEvent.objects.filter(event_id="incident-3").count(), 0)

    def test_traitement_reussi_nappelle_pas_xpending(self) -> None:
        """Le chemin nominal ne doit pas payer le coût d'un appel XPENDING —
        celui-ci n'a lieu que dans le chemin d'échec."""
        fields = {
            "data": json.dumps(
                {
                    "event_id": "ok-1",
                    "type": "PAIEMENT_STATS",
                    "campagne_id": CAMP,
                    "montant_paiement": 100.0,
                    "type_update": "PAIEMENT",
                }
            )
        }
        entries = [(STREAM_KEY, [("1-0", fields)])]
        fake = _FakeRedis({})

        _handle_entries(fake, self.agg, entries)

        self.assertEqual(fake.acked, ["1-0"])
        self.assertEqual(fake.dead_lettered, [])
        self.assertEqual(fake.xpending_calls, 0)
        self.assertEqual(StatsPaiements.objects.get(campagne_id=CAMP).montant_encaisse, Decimal("100.00"))
