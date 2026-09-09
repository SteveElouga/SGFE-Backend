"""Idempotence des upserts `UpdateStatsCampagne`/`UpdateStatsFacturation`/
`UpdateStatsPaiements` face à une double livraison — au niveau BD, contre un
vrai Postgres (`TestCase`/`TransactionTestCase`).

`stats/tests/test_event_consumer.py` couvre déjà le rejeu séquentiel du même
`event_id` pour PAIEMENT_STATS (`test_idempotence_rejeu_meme_event_id`). Ce
fichier étend la même propriété aux deux AUTRES types d'événement
(CAMPAGNE_STATS, FACTURATION_STATS) — chacun a une sémantique de mise à jour
différente (`stats/services.py::AgregateurDashboard`) :

- CAMPAGNE_STATS écrit des valeurs ABSOLUES (`stats.total_abonnes = ...`) :
  idempotent "naturellement" même sans dédup, un rejeu à l'identique ne
  change rien — mais ce n'est vérifié nulle part.
- FACTURATION_STATS/PAIEMENT_STATS s'INCRÉMENTENT (`+= delta`) : SANS la
  déduplication `ProcessedEvent` de `apply_event`, un rejeu doublerait le
  compteur — c'est le cas qui compte vraiment pour la garantie
  "at-least-once ne corrompt pas les stats".

Et surtout : une double livraison RÉELLE n'est pas toujours strictement
séquentielle (deux workers qui traitent le même message redélivré au même
instant, ou un rattrapage au redémarrage qui chevauche une lecture normale) —
`TestConcurrenceDoubleLivraison` fait courir DEUX threads, chacun avec sa
propre connexion Postgres, appliquant EXACTEMENT le même événement en même
temps. C'est la contrainte UNIQUE réelle sur `ProcessedEvent.event_id`
(`models.py`) qui tranche la course, pas une hypothèse de séquentialité du
test — Django's `get_or_create` documente explicitement qu'il gère cette
course en rattrapant l'`IntegrityError` du perdant par un second `get()`.
"""

from __future__ import annotations

import threading
from decimal import Decimal
from unittest import skipUnless

from django.db import connection, connections
from django.test import TestCase, TransactionTestCase

from stats.event_consumer import apply_event
from stats.models import ProcessedEvent, StatsCampagne, StatsFacturation, StatsPaiements
from stats.services import AgregateurDashboard

CAMP = "22222222-2222-2222-2222-222222222222"

_SUR_POSTGRESQL = connection.vendor == "postgresql"
_RAISON_SKIP = "nécessite un vrai Postgres (FORCE_POSTGRES_TESTS=True) — no-op sur SQLite"


class TestIdempotenceCampagneStats(TestCase):
    def setUp(self) -> None:
        self.agg = AgregateurDashboard()

    def test_rejeu_du_meme_event_id_ne_change_rien(self) -> None:
        """CAMPAGNE_STATS écrit des valeurs absolues : rejouer le MÊME
        event_id (donc dédupliqué par `ProcessedEvent`, jamais réappliqué)
        laisse les stats identiques au premier passage."""
        evt = {
            "event_id": "camp-dup-1",
            "type": "CAMPAGNE_STATS",
            "campagne_id": CAMP,
            "nom_campagne": "Septembre",
            "total_abonnes": 50,
            "nb_releves": 20,
            "consommation_totale": 300.0,
        }
        apply_event(self.agg, evt)
        apply_event(self.agg, evt)
        apply_event(self.agg, evt)

        stats = StatsCampagne.objects.get(campagne_id=CAMP)
        self.assertEqual(stats.total_abonnes, 50)
        self.assertEqual(stats.nb_releves, 20)
        self.assertEqual(stats.nb_en_attente, 30)
        self.assertEqual(ProcessedEvent.objects.filter(event_id="camp-dup-1").count(), 1)


class TestIdempotenceFacturationStats(TestCase):
    def setUp(self) -> None:
        self.agg = AgregateurDashboard()

    def test_rejeu_du_meme_event_id_generee_ne_double_pas_le_montant(self) -> None:
        """FACTURATION_STATS/GENEREE incrémente par delta — sans la
        déduplication, rejouer 3 fois tripplerait `total_factures`/
        `montant_total_facture`. Avec elle, une seule application compte."""
        evt = {
            "event_id": "fact-dup-1",
            "type": "FACTURATION_STATS",
            "campagne_id": CAMP,
            "delta_factures": 4,
            "delta_montant": 40000.0,
            "type_update": "GENEREE",
        }
        apply_event(self.agg, evt)
        apply_event(self.agg, evt)  # rejeu
        apply_event(self.agg, evt)  # rejeu

        stats = StatsFacturation.objects.get(campagne_id=CAMP)
        self.assertEqual(stats.total_factures, 4)
        self.assertEqual(stats.montant_total_facture, Decimal("40000.00"))
        self.assertEqual(stats.nb_factures_impayees, 4)
        self.assertEqual(ProcessedEvent.objects.filter(event_id="fact-dup-1").count(), 1)

    def test_rejeu_du_meme_event_id_payee_ne_double_pas_le_compteur(self) -> None:
        """Même propriété pour `type_update="PAYEE"` — un chemin distinct de
        `update_stats_facturation` (branche différente de l'`if/elif`)."""
        apply_event(
            self.agg,
            {
                "event_id": "fact-dup-2a",
                "type": "FACTURATION_STATS",
                "campagne_id": CAMP,
                "delta_factures": 5,
                "delta_montant": 50000.0,
                "type_update": "GENEREE",
            },
        )
        evt_payee = {
            "event_id": "fact-dup-2b",
            "type": "FACTURATION_STATS",
            "campagne_id": CAMP,
            "delta_factures": 2,
            "delta_montant": 0,
            "type_update": "PAYEE",
        }
        apply_event(self.agg, evt_payee)
        apply_event(self.agg, evt_payee)  # rejeu — ne doit pas repasser 2 factures de plus en payées

        stats = StatsFacturation.objects.get(campagne_id=CAMP)
        self.assertEqual(stats.nb_factures_payees, 2)
        self.assertEqual(stats.nb_factures_impayees, 3)


@skipUnless(_SUR_POSTGRESQL, _RAISON_SKIP)
class TestConcurrenceDoubleLivraison(TransactionTestCase):
    """Double livraison RÉELLEMENT concurrente : deux threads, deux
    connexions Postgres distinctes, le même `event_id` — `TransactionTestCase`
    est nécessaire (pas `TestCase`) : chaque thread doit committer pour que
    l'autre voie ses écritures et pour que la contrainte UNIQUE de Postgres
    tranche réellement la course entre les deux `INSERT` concurrents. Sans le
    garde `@skipUnless` (oubli de la PR #250, contrairement à
    `test_grpc_wire_auth_postgres.py` qui l'a) : deux threads ouvrant chacun
    leur propre connexion SQLite en mémoire (`:memory:`) ne partagent PAS
    la même base — le second thread ne verrait jamais la ligne insérée par
    le premier, donc pas de collision, et le test échouerait ou passerait
    pour une raison qui n'a rien à voir avec ce qu'il prétend vérifier."""

    def tearDown(self) -> None:
        # Chaque thread ouvre sa propre connexion Postgres (thread-local,
        # voir `_appliquer_dans_un_thread`) — sans fermeture explicite,
        # `TransactionTestCase` échoue à tronquer les tables en fin de test
        # ("database is being accessed by other users"), même défaut que
        # documenté dans `test_grpc_wire_auth_postgres.py` (notification).
        connections.close_all()

    def test_deux_threads_appliquent_le_meme_evenement_en_meme_temps(self) -> None:
        evt = {
            "event_id": "concu-1",
            "type": "PAIEMENT_STATS",
            "campagne_id": CAMP,
            "montant_paiement": 7500.0,
            "type_update": "PAIEMENT",
        }
        barriere = threading.Barrier(2)
        erreurs: list[BaseException] = []

        def _appliquer_dans_un_thread() -> None:
            try:
                barriere.wait(timeout=5)  # maximise la chance de collision réelle
                apply_event(AgregateurDashboard(), evt)
            except BaseException as exc:  # noqa: BLE001 — capturé pour ressortir côté thread principal
                erreurs.append(exc)
            finally:
                connections.close_all()

        threads = [threading.Thread(target=_appliquer_dans_un_thread) for _ in range(2)]
        for t in threads:
            t.start()
        for t in threads:
            t.join(timeout=10)

        self.assertEqual(
            erreurs, [], f"aucun thread ne doit lever — get_or_create() doit absorber la course : {erreurs}"
        )
        self.assertEqual(ProcessedEvent.objects.filter(event_id="concu-1").count(), 1)
        stats = StatsPaiements.objects.get(campagne_id=CAMP)
        # Une seule application malgré les deux threads concurrents : la
        # contrainte UNIQUE sur ProcessedEvent.event_id a tranché la course,
        # pas un hasard d'ordonnancement.
        self.assertEqual(stats.montant_encaisse, Decimal("7500.00"))
