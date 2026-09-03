"""Tests de ReconciliateurStats — réconciliation nocturne depuis Facturation/Paiement."""

import uuid
from decimal import Decimal

import grpc
from django.test import TestCase

from stats.models import StatsCampagne, StatsFacturation, StatsPaiements
from stats.services import ReconciliateurStats


class _FakeFacturationClient:
    """Double de test : retourne des factures fixées par campagne_id, ou lève."""

    def __init__(self, factures_par_campagne: dict | None = None, echoue_pour: set | None = None) -> None:
        self._factures = factures_par_campagne or {}
        self._echoue_pour = echoue_pour or set()

    def list_factures_par_campagne(self, campagne_id: str) -> list[dict]:
        if campagne_id in self._echoue_pour:
            raise grpc.RpcError("Facturation Service indisponible")
        return self._factures.get(campagne_id, [])


class _FakePaiementClient:
    """Double de test : retourne des paiements fixés par campagne_id, ou lève."""

    def __init__(self, paiements_par_campagne: dict | None = None, echoue_pour: set | None = None) -> None:
        self._paiements = paiements_par_campagne or {}
        self._echoue_pour = echoue_pour or set()

    def list_paiements_par_campagne(self, campagne_id: str) -> list[dict]:
        if campagne_id in self._echoue_pour:
            raise grpc.RpcError("Paiement Service indisponible")
        return self._paiements.get(campagne_id, [])


class ReconcilierCampagneTests(TestCase):
    def setUp(self) -> None:
        self.cid = str(uuid.uuid4())
        StatsCampagne.objects.create(campagne_id=self.cid, nom_campagne="Juin 2026", total_abonnes=3, nb_releves=3)

    def _factures_fixture(self) -> list[dict]:
        return [
            {"facture_id": "f1", "statut": "PAYEE", "montant": 5000.0},
            {"facture_id": "f2", "statut": "PARTIELLE", "montant": 3000.0},
            {"facture_id": "f3", "statut": "IMPAYEE", "montant": 2000.0},
            # Une facture ANNULEE ne doit compter dans rien : ni le total, ni le
            # montant, ni aucun des compteurs par statut.
            {"facture_id": "f4", "statut": "ANNULEE", "montant": 9999.0},
        ]

    def _paiements_fixture(self) -> list[dict]:
        return [
            {"montant": 5000.0, "annule": False},
            {"montant": 1500.0, "annule": False},
            # Un paiement annulé ne doit pas compter dans le montant encaissé.
            {"montant": 999.0, "annule": True},
        ]

    def test_recalcule_stats_facturation_depuis_la_source(self) -> None:
        facturation_client = _FakeFacturationClient({self.cid: self._factures_fixture()})
        paiement_client = _FakePaiementClient({self.cid: self._paiements_fixture()})
        recon = ReconciliateurStats(facturation_client=facturation_client, paiement_client=paiement_client)

        recon.reconcilier_campagne(self.cid)

        stats = StatsFacturation.objects.get(campagne_id=self.cid)
        self.assertEqual(stats.total_factures, 3)  # ANNULEE exclue
        self.assertEqual(stats.montant_total_facture, Decimal("10000.0"))
        self.assertEqual(stats.nb_factures_payees, 1)
        self.assertEqual(stats.nb_factures_partielles, 1)
        self.assertEqual(stats.nb_factures_impayees, 1)

    def test_recalcule_stats_paiements_depuis_la_source(self) -> None:
        facturation_client = _FakeFacturationClient({self.cid: self._factures_fixture()})
        paiement_client = _FakePaiementClient({self.cid: self._paiements_fixture()})
        recon = ReconciliateurStats(facturation_client=facturation_client, paiement_client=paiement_client)

        recon.reconcilier_campagne(self.cid)

        stats = StatsPaiements.objects.get(campagne_id=self.cid)
        self.assertEqual(stats.montant_encaisse, Decimal("6500.0"))  # 5000 + 1500, annulé exclu
        self.assertEqual(stats.montant_impaye, Decimal("3500.0"))  # 10000 - 6500
        self.assertEqual(stats.nb_impayes, 1)
        self.assertEqual(stats.taux_recouvrement, Decimal("65.00"))

    def test_corrige_une_derive_existante(self) -> None:
        """Cas nominal du job : un événement FACTURATION_STATS perdu avait laissé
        une dérive (total_factures resté à zéro) — la réconciliation la corrige."""
        StatsFacturation.objects.create(campagne_id=self.cid, total_factures=0, montant_total_facture=Decimal("0"))
        facturation_client = _FakeFacturationClient({self.cid: self._factures_fixture()})
        paiement_client = _FakePaiementClient({self.cid: []})
        recon = ReconciliateurStats(facturation_client=facturation_client, paiement_client=paiement_client)

        recon.reconcilier_campagne(self.cid)

        stats = StatsFacturation.objects.get(campagne_id=self.cid)
        self.assertEqual(stats.total_factures, 3)
        self.assertEqual(stats.montant_total_facture, Decimal("10000.0"))

    def test_preserve_nb_factures_envoyees_non_derivable_de_facturation(self) -> None:
        """nb_factures_envoyees vient de l'envoi WhatsApp (Notification Service) :
        Facturation Service n'en garde aucune trace durable, la réconciliation
        ne doit donc jamais l'écraser."""
        StatsFacturation.objects.create(campagne_id=self.cid, nb_factures_envoyees=7)
        facturation_client = _FakeFacturationClient({self.cid: self._factures_fixture()})
        paiement_client = _FakePaiementClient({self.cid: []})
        recon = ReconciliateurStats(facturation_client=facturation_client, paiement_client=paiement_client)

        recon.reconcilier_campagne(self.cid)

        stats = StatsFacturation.objects.get(campagne_id=self.cid)
        self.assertEqual(stats.nb_factures_envoyees, 7)

    def test_ne_fabrique_pas_de_ligne_pour_une_campagne_sans_facture(self) -> None:
        """Une campagne EN_COURS (encore aucune facture) ne doit pas se voir
        attribuer une ligne StatsFacturation à zéro par la réconciliation —
        `facturation=None` au dashboard a un sens différent de `stats à zéro`."""
        facturation_client = _FakeFacturationClient({self.cid: []})
        paiement_client = _FakePaiementClient({self.cid: []})
        recon = ReconciliateurStats(facturation_client=facturation_client, paiement_client=paiement_client)

        recon.reconcilier_campagne(self.cid)

        self.assertFalse(StatsFacturation.objects.filter(campagne_id=self.cid).exists())
        self.assertFalse(StatsPaiements.objects.filter(campagne_id=self.cid).exists())

    def test_facturation_indisponible_leve_et_ne_modifie_rien(self) -> None:
        StatsFacturation.objects.create(
            campagne_id=self.cid, total_factures=5, montant_total_facture=Decimal("12345.00")
        )
        facturation_client = _FakeFacturationClient(echoue_pour={self.cid})
        paiement_client = _FakePaiementClient({self.cid: []})
        recon = ReconciliateurStats(facturation_client=facturation_client, paiement_client=paiement_client)

        with self.assertRaises(grpc.RpcError):
            recon.reconcilier_campagne(self.cid)

        # Les stats existantes (correctes ou non) ne doivent pas être écrasées
        # par des zéros trompeurs simplement parce que la source est injoignable.
        stats = StatsFacturation.objects.get(campagne_id=self.cid)
        self.assertEqual(stats.total_factures, 5)
        self.assertEqual(stats.montant_total_facture, Decimal("12345.00"))

    def test_paiement_indisponible_leve(self) -> None:
        facturation_client = _FakeFacturationClient({self.cid: self._factures_fixture()})
        paiement_client = _FakePaiementClient(echoue_pour={self.cid})
        recon = ReconciliateurStats(facturation_client=facturation_client, paiement_client=paiement_client)

        with self.assertRaises(grpc.RpcError):
            recon.reconcilier_campagne(self.cid)


class ReconcilierToutesCampagnesTests(TestCase):
    def setUp(self) -> None:
        self.cid_ok = str(uuid.uuid4())
        self.cid_echec = str(uuid.uuid4())
        StatsCampagne.objects.create(campagne_id=self.cid_ok, nom_campagne="OK", total_abonnes=1, nb_releves=1)
        StatsCampagne.objects.create(campagne_id=self.cid_echec, nom_campagne="KO", total_abonnes=1, nb_releves=1)

    def test_une_campagne_en_echec_ne_bloque_pas_les_autres(self) -> None:
        factures = {self.cid_ok: [{"facture_id": "f1", "statut": "PAYEE", "montant": 1000.0}]}
        facturation_client = _FakeFacturationClient(factures, echoue_pour={self.cid_echec})
        paiement_client = _FakePaiementClient({self.cid_ok: []})
        recon = ReconciliateurStats(facturation_client=facturation_client, paiement_client=paiement_client)

        nb_ok, nb_echecs = recon.reconcilier_toutes_campagnes()

        self.assertEqual(nb_ok, 1)
        self.assertEqual(nb_echecs, 1)
        self.assertTrue(StatsFacturation.objects.filter(campagne_id=self.cid_ok, total_factures=1).exists())
        self.assertFalse(StatsFacturation.objects.filter(campagne_id=self.cid_echec).exists())
