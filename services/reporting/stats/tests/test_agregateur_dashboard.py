"""Tests unitaires de `AgregateurDashboard` (voir `stats/services.py`).

Complète `test_services.py` (TestCase, vraies valeurs persistées en base) par
des tests qui isolent la logique de l'agrégateur de la couche persistance :
les 3 repositories sont remplacés par des `MagicMock` et aucun accès BD n'a
lieu ici (`SimpleTestCase`). Deux axes, choisis parce qu'ils portent une
vraie logique métier et ne sont pas observables au niveau des valeurs
persistées seules :

- l'**idempotence des upserts** : chaque mise à jour passe par
  `get_or_create`/`save`, jamais par une création brute, et cible toujours le
  même `campagne_id` quel que soit le nombre d'appels ;
- la **dégradation "sous-bloc nul"** : quand un domaine (campagne,
  facturation, paiements) n'a pas encore reçu de mise à jour, l'agrégateur ne
  fabrique jamais de ligne à zéro à sa place et ne consulte même pas les
  repositories des autres domaines quand ce n'est pas nécessaire.
"""

from __future__ import annotations

import uuid
from decimal import Decimal
from typing import Any
from unittest.mock import MagicMock, patch

from django.core.exceptions import ObjectDoesNotExist
from django.test import SimpleTestCase

from stats.models import StatsCampagne, StatsFacturation, StatsPaiements
from stats.services import AgregateurDashboard


def _campagne(campagne_id: str, **kwargs: Any) -> StatsCampagne:
    """Construit un `StatsCampagne` en mémoire — jamais persisté."""
    return StatsCampagne(campagne_id=campagne_id, **kwargs)


def _facturation(campagne_id: str, **kwargs: Any) -> StatsFacturation:
    """Construit un `StatsFacturation` en mémoire — jamais persisté."""
    return StatsFacturation(campagne_id=campagne_id, **kwargs)


def _paiements(campagne_id: str, **kwargs: Any) -> StatsPaiements:
    """Construit un `StatsPaiements` en mémoire — jamais persisté."""
    return StatsPaiements(campagne_id=campagne_id, **kwargs)


class _AvecRepositoriesMockesTestCase(SimpleTestCase):
    """Instancie un `AgregateurDashboard` dont les 3 repositories sont des
    `MagicMock` — aucun accès BD, seule la logique de l'agrégateur s'exécute.
    """

    def setUp(self) -> None:
        super().setUp()
        patcher_campagne = patch("stats.services.StatsCampagneRepository")
        patcher_facturation = patch("stats.services.StatsFacturationRepository")
        patcher_paiements = patch("stats.services.StatsPaiementsRepository")
        mock_campagne_cls = patcher_campagne.start()
        mock_facturation_cls = patcher_facturation.start()
        mock_paiements_cls = patcher_paiements.start()
        self.addCleanup(patcher_campagne.stop)
        self.addCleanup(patcher_facturation.stop)
        self.addCleanup(patcher_paiements.stop)

        self.repo_campagne = MagicMock(name="StatsCampagneRepository")
        self.repo_facturation = MagicMock(name="StatsFacturationRepository")
        self.repo_paiements = MagicMock(name="StatsPaiementsRepository")
        mock_campagne_cls.return_value = self.repo_campagne
        mock_facturation_cls.return_value = self.repo_facturation
        mock_paiements_cls.return_value = self.repo_paiements

        self.agg = AgregateurDashboard()
        self.cid = str(uuid.uuid4())


class UpdateStatsCampagneIdempotenceTests(_AvecRepositoriesMockesTestCase):
    def test_upsert_passe_par_get_or_create_jamais_par_une_creation_brute(self) -> None:
        self.repo_campagne.get_or_create.return_value = _campagne(self.cid)
        self.agg.update_stats_campagne(self.cid, "Juin", 50, 40, 1200)
        self.repo_campagne.get_or_create.assert_called_once_with(self.cid)

    def test_le_resultat_de_get_or_create_est_persiste_via_save(self) -> None:
        stats = _campagne(self.cid)
        self.repo_campagne.get_or_create.return_value = stats
        self.repo_campagne.save.return_value = stats
        resultat = self.agg.update_stats_campagne(self.cid, "Juin", 50, 40, 1200)
        self.repo_campagne.save.assert_called_once_with(stats)
        self.assertIs(resultat, stats)

    def test_appels_repetes_ciblent_toujours_le_meme_campagne_id(self) -> None:
        """Deux événements successifs pour la même campagne doivent upserter
        la même ligne — jamais deux lignes distinctes pour un même id."""
        self.repo_campagne.get_or_create.return_value = _campagne(self.cid)
        self.agg.update_stats_campagne(self.cid, "Mai", 50, 10, 100)
        self.agg.update_stats_campagne(self.cid, "Mai", 50, 45, 900)
        self.assertEqual(self.repo_campagne.get_or_create.call_count, 2)
        for appel in self.repo_campagne.get_or_create.call_args_list:
            self.assertEqual(appel.args, (self.cid,))


class UpdateStatsFacturationIdempotenceTests(_AvecRepositoriesMockesTestCase):
    def test_upsert_passe_par_get_or_create_jamais_par_une_creation_brute(self) -> None:
        self.repo_facturation.get_or_create.return_value = _facturation(self.cid)
        self.agg.update_stats_facturation(self.cid, delta_factures=5, delta_montant=25000, type_update="GENEREE")
        self.repo_facturation.get_or_create.assert_called_once_with(self.cid)

    def test_le_resultat_de_get_or_create_est_persiste_via_save(self) -> None:
        stats = _facturation(self.cid)
        self.repo_facturation.get_or_create.return_value = stats
        self.repo_facturation.save.return_value = stats
        resultat = self.agg.update_stats_facturation(self.cid, 5, 25000, "GENEREE")
        self.repo_facturation.save.assert_called_once_with(stats)
        self.assertIs(resultat, stats)


class UpdateStatsPaiementsIdempotenceEtDegradationTests(_AvecRepositoriesMockesTestCase):
    def test_upsert_passe_par_get_or_create_jamais_par_une_creation_brute(self) -> None:
        self.repo_paiements.get_or_create.return_value = _paiements(self.cid)
        self.repo_facturation.get_or_none.return_value = None
        self.agg.update_stats_paiements(self.cid, montant_paiement=1000, type_update="PAIEMENT")
        self.repo_paiements.get_or_create.assert_called_once_with(self.cid)

    def test_sans_facturation_prealable_les_derives_degradent_a_zero_sans_lever(self) -> None:
        """Un paiement peut arriver avant tout événement de facturation (le
        câblage événementiel amont n'est pas encore fait pour tous les
        producteurs, voir CLAUDE.md du service) : les champs dérivés du
        montant facturé doivent se dégrader à zéro plutôt que de lever une
        exception ou de produire un montant impayé négatif."""
        self.repo_facturation.get_or_none.return_value = None
        stats = _paiements(self.cid)
        self.repo_paiements.get_or_create.return_value = stats
        self.repo_paiements.save.return_value = stats

        resultat = self.agg.update_stats_paiements(self.cid, montant_paiement=5000, type_update="PAIEMENT")

        self.assertEqual(resultat.montant_encaisse, Decimal("5000"))
        self.assertEqual(resultat.montant_impaye, Decimal("0"))
        self.assertEqual(resultat.taux_recouvrement, Decimal("0"))
        self.assertEqual(resultat.nb_impayes, 0)

    def test_avec_facturation_prealable_les_derives_se_recalculent_depuis_celle_ci(self) -> None:
        facturation = _facturation(self.cid, montant_total_facture=Decimal("20000"), nb_factures_impayees=4)
        self.repo_facturation.get_or_none.return_value = facturation
        stats = _paiements(self.cid)
        self.repo_paiements.get_or_create.return_value = stats
        self.repo_paiements.save.return_value = stats

        resultat = self.agg.update_stats_paiements(self.cid, montant_paiement=5000, type_update="PAIEMENT")

        self.repo_facturation.get_or_none.assert_called_once_with(self.cid)
        self.assertEqual(resultat.montant_impaye, Decimal("15000"))
        self.assertEqual(resultat.taux_recouvrement, Decimal("25.00"))
        self.assertEqual(resultat.nb_impayes, 4)


class GetDashboardDegradationTests(_AvecRepositoriesMockesTestCase):
    def test_sans_aucune_campagne_ne_consulte_meme_pas_facturation_ni_paiements(self) -> None:
        """Dégradation sous-bloc nul : quand rien n'a encore été poussé,
        l'agrégateur renvoie un dashboard entièrement vide sans même
        interroger les repositories de facturation/paiements."""
        self.repo_campagne.get_derniere.return_value = None

        dashboard = self.agg.get_dashboard()

        self.assertIsNone(dashboard.campagne)
        self.assertIsNone(dashboard.facturation)
        self.assertIsNone(dashboard.paiements)
        self.repo_facturation.get_or_none.assert_not_called()
        self.repo_paiements.get_or_none.assert_not_called()

    def test_campagne_connue_sans_facturation_ni_paiements_pousses(self) -> None:
        """La campagne la plus récente peut ne pas encore avoir de
        facturation/paiements (câblage événementiel partiel, ou campagne
        toujours EN_COURS) — chaque sous-bloc dégrade indépendamment à None."""
        campagne = _campagne(self.cid, nom_campagne="Juillet")
        self.repo_campagne.get_derniere.return_value = campagne
        self.repo_facturation.get_or_none.return_value = None
        self.repo_paiements.get_or_none.return_value = None

        dashboard = self.agg.get_dashboard()

        self.assertIs(dashboard.campagne, campagne)
        self.assertIsNone(dashboard.facturation)
        self.assertIsNone(dashboard.paiements)
        self.repo_facturation.get_or_none.assert_called_once_with(self.cid)
        self.repo_paiements.get_or_none.assert_called_once_with(self.cid)


class GetStatsCompletesDegradationTests(_AvecRepositoriesMockesTestCase):
    def test_chaque_sous_bloc_degrade_independamment_des_autres(self) -> None:
        """`get_stats_completes` ne couple jamais la présence d'un domaine à
        celle d'un autre : chaque sous-bloc reflète uniquement ce que SON
        repository a — jamais dérivé de l'état des deux autres."""
        cas: list[tuple[str, StatsCampagne | None, StatsFacturation | None, StatsPaiements | None]] = [
            ("rien_n_a_ete_pousse", None, None, None),
            ("seule_la_campagne_a_ete_poussee", _campagne(self.cid), None, None),
            (
                "campagne_et_facturation_poussees_paiements_absents",
                _campagne(self.cid),
                _facturation(self.cid),
                None,
            ),
            (
                "seuls_les_paiements_sont_disponibles",
                None,
                None,
                _paiements(self.cid),
            ),
        ]
        for label, campagne, facturation, paiements in cas:
            with self.subTest(label):
                self.repo_campagne.get_or_none.return_value = campagne
                self.repo_facturation.get_or_none.return_value = facturation
                self.repo_paiements.get_or_none.return_value = paiements

                dashboard = self.agg.get_stats_completes(self.cid)

                self.assertIs(dashboard.campagne, campagne)
                self.assertIs(dashboard.facturation, facturation)
                self.assertIs(dashboard.paiements, paiements)

    def test_ne_leve_jamais_meme_quand_les_3_domaines_sont_absents(self) -> None:
        self.repo_campagne.get_or_none.return_value = None
        self.repo_facturation.get_or_none.return_value = None
        self.repo_paiements.get_or_none.return_value = None
        try:
            self.agg.get_stats_completes(self.cid)
        except ObjectDoesNotExist:
            self.fail("get_stats_completes ne doit jamais lever pour une campagne inconnue")


class GetStatsCampagneTests(_AvecRepositoriesMockesTestCase):
    def test_propage_object_does_not_exist_sans_consulter_les_autres_repos(self) -> None:
        """Contrairement à `get_stats_completes`, `get_stats_campagne` doit
        laisser filtrer l'exception du repository — et ne doit jamais, pour
        la masquer, interroger facturation/paiements à sa place."""
        self.repo_campagne.get.side_effect = ObjectDoesNotExist
        with self.assertRaises(ObjectDoesNotExist):
            self.agg.get_stats_campagne(self.cid)
        self.repo_facturation.get_or_none.assert_not_called()
        self.repo_paiements.get_or_none.assert_not_called()

    def test_campagne_connue_renvoie_directement_l_objet_du_repository(self) -> None:
        stats = _campagne(self.cid)
        self.repo_campagne.get.return_value = stats
        self.assertIs(self.agg.get_stats_campagne(self.cid), stats)


class GetStatsGlobalesTests(_AvecRepositoriesMockesTestCase):
    def test_aucune_campagne_degrade_a_des_totaux_zero_sans_lever(self) -> None:
        """Une instance fraîche (aucune campagne connue) ne doit jamais
        lever sur `sum()` d'une liste vide — dégradation à zéro, pas d'erreur."""
        self.repo_campagne.list_all.return_value = []
        self.repo_facturation.list_all.return_value = []
        self.repo_paiements.list_all.return_value = []

        globales = self.agg.get_stats_globales()

        self.assertEqual(globales.historique_campagnes, [])
        self.assertEqual(globales.consommation_totale_globale, Decimal("0"))
        self.assertEqual(globales.montant_total_facture_global, Decimal("0"))
        self.assertEqual(globales.montant_total_encaisse_global, Decimal("0"))

    def test_agrege_bien_les_3_domaines_depuis_les_repositories(self) -> None:
        c1, c2 = (
            _campagne(str(uuid.uuid4()), consommation_totale=Decimal("100")),
            _campagne(str(uuid.uuid4()), consommation_totale=Decimal("200")),
        )
        self.repo_campagne.list_all.return_value = [c1, c2]
        self.repo_facturation.list_all.return_value = [_facturation(self.cid, montant_total_facture=Decimal("250000"))]
        self.repo_paiements.list_all.return_value = [_paiements(self.cid, montant_encaisse=Decimal("100000"))]

        globales = self.agg.get_stats_globales()

        self.assertEqual(globales.historique_campagnes, [c1, c2])
        self.assertEqual(globales.consommation_totale_globale, Decimal("300"))
        self.assertEqual(globales.montant_total_facture_global, Decimal("250000"))
        self.assertEqual(globales.montant_total_encaisse_global, Decimal("100000"))
