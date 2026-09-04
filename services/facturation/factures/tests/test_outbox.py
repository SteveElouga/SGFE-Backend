"""Tests de l'outbox transactionnelle (facturation → paiement).

Remplace le dual-write best-effort (facture committée, appel gRPC ignoré en
cas d'échec) par un pattern *transactional outbox* : l'événement
`FACTURE_GENEREE` est écrit dans LA MÊME transaction que la `Facture`, et un
relais planifié (`factures/schedulers.py`) le rejoue jusqu'à ce que
`InitialiserSolde` réussisse. Voir `factures/models.py::OutboxEvent` pour le
contrat complet.

Quatre familles de tests, dans l'ordre demandé par la revue :
1. écriture atomique outbox + facture (y compris le rollback conjoint) ;
2. relais qui traite un lot ;
3. idempotence si le relais retente un événement déjà `ENVOYE` ;
4. comportement du verrou consultatif PostgreSQL du scheduler.
"""

import datetime
import tempfile
from decimal import Decimal
from unittest.mock import MagicMock, patch

from django.db.models.signals import post_save
from django.test import TestCase

from factures.models import Facture, OutboxEvent, StatutOutboxEvent, TypeEvenementOutbox
from factures.pdf_generator import InfosSociete
from factures.repositories import OutboxEventRepository
from factures.services import MAX_TENTATIVES_OUTBOX, FactureService, OutboxRelayService, ReleveData, TarifService

from .helpers import service_avec_clients_mockes

ABONNE = "abo-outbox-1"
CAMPAGNE = "camp-outbox-1"


def _releve(abonne_id: str = ABONNE, ancien: float = 100.0, nouveau: float = 110.0) -> ReleveData:
    return ReleveData(
        abonne_id=abonne_id,
        ancien_index=ancien,
        nouveau_index=nouveau,
        consommation=nouveau - ancien,
        date_releve="2026-08-15",
    )


class OutboxEventRepositoryTests(TestCase):
    """Tests unitaires du dépôt — pas de logique métier, juste lecture/écriture."""

    def setUp(self) -> None:
        self.repo = OutboxEventRepository()

    def test_create_est_en_attente_par_defaut(self) -> None:
        event = self.repo.create(
            type_evenement=TypeEvenementOutbox.FACTURE_GENEREE,
            payload={"facture_id": "f-1", "abonne_id": "a-1"},
        )
        self.assertEqual(event.statut, StatutOutboxEvent.EN_ATTENTE)
        self.assertEqual(event.tentatives, 0)
        self.assertIsNone(event.envoye_at)
        self.assertEqual(event.payload["facture_id"], "f-1")

    def test_list_en_attente_ignore_les_evenements_traites(self) -> None:
        en_attente = self.repo.create(type_evenement=TypeEvenementOutbox.FACTURE_GENEREE, payload={})
        envoye = self.repo.create(type_evenement=TypeEvenementOutbox.FACTURE_GENEREE, payload={})
        self.repo.marquer_envoye(envoye)

        resultat = self.repo.list_en_attente()

        self.assertEqual([e.id for e in resultat], [en_attente.id])

    def test_list_en_attente_ordre_fifo(self) -> None:
        """Le plus ancien événement bloqué doit être rejoué en premier."""
        premier = OutboxEvent.objects.create(type_evenement=TypeEvenementOutbox.FACTURE_GENEREE, payload={})
        # created_at est auto_now_add : on force un écart explicite pour ne pas
        # dépendre de la résolution de l'horloge entre deux create() successifs.
        premier.created_at = datetime.datetime(2026, 1, 1, tzinfo=datetime.timezone.utc)
        premier.save(update_fields=["created_at"])
        second = OutboxEvent.objects.create(type_evenement=TypeEvenementOutbox.FACTURE_GENEREE, payload={})
        second.created_at = datetime.datetime(2026, 1, 2, tzinfo=datetime.timezone.utc)
        second.save(update_fields=["created_at"])

        resultat = self.repo.list_en_attente()

        self.assertEqual([e.id for e in resultat], [premier.id, second.id])

    def test_list_en_attente_respecte_la_limite(self) -> None:
        for _ in range(3):
            self.repo.create(type_evenement=TypeEvenementOutbox.FACTURE_GENEREE, payload={})
        self.assertEqual(len(self.repo.list_en_attente(limit=2)), 2)

    def test_marquer_envoye(self) -> None:
        event = self.repo.create(type_evenement=TypeEvenementOutbox.FACTURE_GENEREE, payload={})
        self.repo.marquer_envoye(event)
        event.refresh_from_db()
        self.assertEqual(event.statut, StatutOutboxEvent.ENVOYE)
        self.assertIsNotNone(event.envoye_at)

    def test_enregistrer_echec_reste_en_attente_sous_le_plafond(self) -> None:
        event = self.repo.create(type_evenement=TypeEvenementOutbox.FACTURE_GENEREE, payload={})
        self.repo.enregistrer_echec(event, max_tentatives=5)
        event.refresh_from_db()
        self.assertEqual(event.tentatives, 1)
        self.assertEqual(event.statut, StatutOutboxEvent.EN_ATTENTE)

    def test_enregistrer_echec_passe_en_echec_au_plafond(self) -> None:
        event = self.repo.create(type_evenement=TypeEvenementOutbox.FACTURE_GENEREE, payload={})
        event.tentatives = 4
        event.save(update_fields=["tentatives"])
        self.repo.enregistrer_echec(event, max_tentatives=5)
        event.refresh_from_db()
        self.assertEqual(event.tentatives, 5)
        self.assertEqual(event.statut, StatutOutboxEvent.ECHEC)


class EcritureTransactionnelleOutboxTests(TestCase):
    """L'événement outbox doit naître dans LA MÊME transaction que la facture."""

    def setUp(self) -> None:
        self.svc = service_avec_clients_mockes()
        TarifService().update_tarif(Decimal("500.00"), datetime.date(2026, 8, 1))
        self.societe = InfosSociete(nom="SGFE Test", adresse="Yaoundé", telephone="+237000000000")

    def _generer(self, svc: FactureService, releves: list[ReleveData], campagne_id: str = CAMPAGNE) -> list[Facture]:
        with tempfile.TemporaryDirectory() as tmpdir:
            with patch("factures.services.settings") as mock_settings:
                mock_settings.PDF_STORAGE_DIR = tmpdir
                mock_settings.DEFAULT_DELAI_PAIEMENT_JOURS = 5
                return svc.generer_factures(
                    campagne_id=campagne_id,
                    releves=releves,
                    delai_paiement_jours=5,
                    societe=self.societe,
                )

    def test_generer_factures_ecrit_un_evenement_outbox_par_facture(self) -> None:
        factures = self._generer(self.svc, [_releve(), _releve("abo-outbox-2", 200.0, 225.0)])
        self.assertEqual(OutboxEvent.objects.count(), 2)
        ids_factures = {str(f.id) for f in factures}
        ids_outbox = {e.payload["facture_id"] for e in OutboxEvent.objects.all()}
        self.assertEqual(ids_factures, ids_outbox)

    def test_payload_porte_tout_ce_dont_initialiser_solde_a_besoin(self) -> None:
        (facture,) = self._generer(self.svc, [_releve()])
        event = OutboxEvent.objects.get(payload__facture_id=str(facture.id))
        self.assertEqual(event.type_evenement, TypeEvenementOutbox.FACTURE_GENEREE)
        self.assertEqual(event.payload["abonne_id"], ABONNE)
        self.assertEqual(event.payload["campagne_id"], CAMPAGNE)
        self.assertEqual(event.payload["montant_total"], float(facture.montant))
        self.assertEqual(event.payload["prix_m3"], float(facture.prix_m3))
        self.assertEqual(event.payload["date_limite_paiement"], facture.date_limite_paiement.isoformat())

    def test_appel_immediat_reussi_marque_l_evenement_envoye(self) -> None:
        """Le best-effort synchrone reste une optimisation de latence : s'il
        réussit, l'outbox n'a plus besoin d'être rejouée par le relais."""
        self.svc._paiement_client.initialiser_solde.return_value = True  # type: ignore[attr-defined]
        (facture,) = self._generer(self.svc, [_releve()])
        event = OutboxEvent.objects.get(payload__facture_id=str(facture.id))
        self.assertEqual(event.statut, StatutOutboxEvent.ENVOYE)
        self.assertIsNotNone(event.envoye_at)

    def test_appel_immediat_en_echec_laisse_l_evenement_en_attente(self) -> None:
        """C'est précisément le scénario que l'outbox corrige : Paiement
        Service indisponible à la génération ne doit plus produire de facture
        orpheline — la facture est quand même créée, l'événement attend le
        relais."""
        self.svc._paiement_client.initialiser_solde.return_value = False  # type: ignore[attr-defined]
        (facture,) = self._generer(self.svc, [_releve()])
        self.assertTrue(Facture.objects.filter(id=facture.id).exists())
        event = OutboxEvent.objects.get(payload__facture_id=str(facture.id))
        self.assertEqual(event.statut, StatutOutboxEvent.EN_ATTENTE)
        self.assertEqual(event.tentatives, 0)

    def test_creer_regularisation_ecrit_un_evenement_outbox(self) -> None:
        self.svc._paiement_client.initialiser_solde.return_value = False  # type: ignore[attr-defined]
        facture = self.svc.creer_regularisation(abonne_id="ab-reg", montant=7_500, motif="Reliquat")
        event = OutboxEvent.objects.get(payload__facture_id=str(facture.id))
        self.assertEqual(event.payload["campagne_id"], "")
        self.assertEqual(event.payload["montant_total"], 7500.0)
        self.assertEqual(event.statut, StatutOutboxEvent.EN_ATTENTE)

    def test_regenerer_facture_ecrit_un_evenement_outbox_pour_la_nouvelle(self) -> None:
        ancienne = Facture.objects.create(
            numero_facture="FACT-2026-08-0001",
            abonne_id=ABONNE,
            campagne_id=CAMPAGNE,
            ancien_index=Decimal("100"),
            nouveau_index=Decimal("120"),
            consommation=Decimal("20"),
            prix_m3=Decimal("500"),
            montant=Decimal("10000"),
            date_releve=datetime.date(2026, 8, 1),
            date_limite_paiement=datetime.date(2026, 8, 6),
        )
        self.svc._campagne_client.list_releves.return_value = [  # type: ignore[attr-defined]
            {
                "abonne_id": ABONNE,
                "ancien_index": 100.0,
                "nouveau_index": 130.0,
                "consommation": 30.0,
                "date_releve": "2026-08-01",
                "statut": "RELEVE",
            }
        ]
        self.svc._paiement_client.initialiser_solde.return_value = False  # type: ignore[attr-defined]

        with tempfile.TemporaryDirectory() as tmpdir:
            with patch("factures.services.settings") as mock_settings:
                mock_settings.PDF_STORAGE_DIR = tmpdir
                mock_settings.DEFAULT_DELAI_PAIEMENT_JOURS = 5
                _annulee, nouvelle = self.svc.regenerer_facture(
                    str(ancienne.id), motif="Correction", regenere_par="admin", delai_paiement_jours=15
                )

        event = OutboxEvent.objects.get(payload__facture_id=str(nouvelle.id))
        self.assertEqual(event.statut, StatutOutboxEvent.EN_ATTENTE)
        self.assertEqual(event.payload["montant_total"], float(nouvelle.montant))

    def test_rollback_annule_facture_et_evenement_outbox_ensemble(self) -> None:
        """Un crash après l'écriture outbox mais avant le commit doit annuler
        LES DEUX écritures — pas seulement l'une des deux. C'est cette
        atomicité, et non l'appel gRPC best-effort, qui garantit qu'aucune
        facture ne peut exister sans son événement outbox."""

        def _lever_apres_creation(sender: object, instance: OutboxEvent, created: bool, **kwargs: object) -> None:
            if created:
                raise RuntimeError("crash simulé juste après l'écriture outbox, avant le commit")

        post_save.connect(_lever_apres_creation, sender=OutboxEvent, dispatch_uid="test-rollback-outbox")
        try:
            with self.assertRaises(RuntimeError):
                self._generer(self.svc, [_releve()])
        finally:
            post_save.disconnect(sender=OutboxEvent, dispatch_uid="test-rollback-outbox")

        self.assertEqual(Facture.objects.filter(abonne_id=ABONNE).count(), 0)
        self.assertEqual(OutboxEvent.objects.count(), 0)

    def test_rollback_regularisation_annule_facture_et_evenement_outbox(self) -> None:
        """Même garantie que pour generer_factures, sur le chemin régularisation."""

        def _lever_apres_creation(sender: object, instance: OutboxEvent, created: bool, **kwargs: object) -> None:
            if created:
                raise RuntimeError("crash simulé — régularisation")

        post_save.connect(_lever_apres_creation, sender=OutboxEvent, dispatch_uid="test-rollback-outbox-reg")
        try:
            with self.assertRaises(RuntimeError):
                self.svc.creer_regularisation(abonne_id="ab-rollback", montant=1_000, motif="Test rollback")
        finally:
            post_save.disconnect(sender=OutboxEvent, dispatch_uid="test-rollback-outbox-reg")

        self.assertEqual(Facture.objects.filter(abonne_id="ab-rollback").count(), 0)
        self.assertEqual(OutboxEvent.objects.count(), 0)


class OutboxRelayServiceTests(TestCase):
    """Le relais planifié (factures/schedulers.py) délègue tout à ce service."""

    def setUp(self) -> None:
        self.repo = OutboxEventRepository()
        self.paiement_client = MagicMock()
        self.svc = OutboxRelayService(paiement_client=self.paiement_client)

    def _event(self, facture_id: str = "f-1") -> OutboxEvent:
        return self.repo.create(
            type_evenement=TypeEvenementOutbox.FACTURE_GENEREE,
            payload={
                "facture_id": facture_id,
                "abonne_id": "ab-1",
                "campagne_id": "camp-1",
                "montant_total": 1000.0,
                "prix_m3": 500.0,
                "date_limite_paiement": "2026-08-10",
            },
        )

    def test_relayer_lot_marque_envoye_sur_succes(self) -> None:
        self.paiement_client.initialiser_solde.return_value = True
        event = self._event()

        envoyes, echoues, abandonnes = self.svc.relayer_lot()

        self.assertEqual((envoyes, echoues, abandonnes), (1, 0, 0))
        event.refresh_from_db()
        self.assertEqual(event.statut, StatutOutboxEvent.ENVOYE)
        self.paiement_client.initialiser_solde.assert_called_once_with(
            facture_id="f-1",
            abonne_id="ab-1",
            montant_total=1000.0,
            date_limite_paiement="2026-08-10",
            campagne_id="camp-1",
        )

    def test_relayer_lot_traite_tout_le_lot(self) -> None:
        self.paiement_client.initialiser_solde.return_value = True
        self._event("f-1")
        self._event("f-2")
        self._event("f-3")

        envoyes, _echoues, _abandonnes = self.svc.relayer_lot()

        self.assertEqual(envoyes, 3)
        self.assertEqual(OutboxEvent.objects.filter(statut=StatutOutboxEvent.ENVOYE).count(), 3)

    def test_relayer_lot_respecte_la_limite(self) -> None:
        self.paiement_client.initialiser_solde.return_value = True
        self._event("f-1")
        self._event("f-2")

        envoyes, _e, _a = self.svc.relayer_lot(limit=1)

        self.assertEqual(envoyes, 1)
        self.assertEqual(OutboxEvent.objects.filter(statut=StatutOutboxEvent.EN_ATTENTE).count(), 1)

    def test_relayer_lot_incremente_les_tentatives_sur_echec(self) -> None:
        self.paiement_client.initialiser_solde.return_value = False
        event = self._event()

        envoyes, echoues, abandonnes = self.svc.relayer_lot()

        self.assertEqual((envoyes, echoues, abandonnes), (0, 1, 0))
        event.refresh_from_db()
        self.assertEqual(event.statut, StatutOutboxEvent.EN_ATTENTE)
        self.assertEqual(event.tentatives, 1)

    def test_relayer_lot_abandonne_au_plafond(self) -> None:
        """Passé MAX_TENTATIVES_OUTBOX échecs, l'événement doit être marqué
        ECHEC (terminal) et journalisé comme une alerte — jamais rejoué à
        l'infini."""
        self.paiement_client.initialiser_solde.return_value = False
        event = self._event()

        for _ in range(MAX_TENTATIVES_OUTBOX - 1):
            self.svc.relayer_lot()
        event.refresh_from_db()
        self.assertEqual(event.statut, StatutOutboxEvent.EN_ATTENTE, "pas encore au plafond")

        with self.assertLogs("factures.services", level="ERROR") as logs:
            envoyes, echoues, abandonnes = self.svc.relayer_lot()

        self.assertEqual((envoyes, echoues, abandonnes), (0, 0, 1))
        event.refresh_from_db()
        self.assertEqual(event.statut, StatutOutboxEvent.ECHEC)
        self.assertEqual(event.tentatives, MAX_TENTATIVES_OUTBOX)
        self.assertTrue(any("abandonné" in message for message in logs.output))

        # Un événement ECHEC n'est plus jamais repris par le relais.
        self.paiement_client.initialiser_solde.reset_mock()
        self.svc.relayer_lot()
        self.paiement_client.initialiser_solde.assert_not_called()

    def test_relayer_lot_ignore_un_type_evenement_inconnu(self) -> None:
        event = self.repo.create(type_evenement="TYPE_FUTUR_INCONNU", payload={})
        with self.assertLogs("factures.services", level="WARNING"):
            envoyes, echoues, abandonnes = self.svc.relayer_lot()
        self.assertEqual((envoyes, echoues, abandonnes), (0, 0, 0))
        self.paiement_client.initialiser_solde.assert_not_called()
        event.refresh_from_db()
        self.assertEqual(event.statut, StatutOutboxEvent.EN_ATTENTE, "laissé tel quel, pas perdu")

    def test_relayer_lot_ne_retraite_jamais_un_evenement_deja_envoye(self) -> None:
        """`list_en_attente` ne renvoie que les événements EN_ATTENTE : un
        événement ENVOYE ne peut plus être présenté au relais."""
        self.paiement_client.initialiser_solde.return_value = True
        event = self._event()
        self.svc.relayer_lot()
        event.refresh_from_db()
        self.assertEqual(event.statut, StatutOutboxEvent.ENVOYE)

        self.paiement_client.initialiser_solde.reset_mock()
        envoyes, echoues, abandonnes = self.svc.relayer_lot()

        self.assertEqual((envoyes, echoues, abandonnes), (0, 0, 0))
        self.paiement_client.initialiser_solde.assert_not_called()

    def test_idempotence_apres_redemarrage_du_relais_sur_evenement_deja_envoye(self) -> None:
        """Scénario de bout en bout : le relais traite l'événement une
        première fois (ENVOYE), puis un redémarrage du relais le retrouve
        EN_ATTENTE (bookkeeping perdu entre l'appel gRPC réussi et la mise à
        jour du statut outbox — le pire cas réaliste). Le second passage
        rejoue `InitialiserSolde` : côté Paiement Service, ce RPC est
        idempotent par facture_id (voir
        paiements/services.py::PaiementService.initialiser_solde et
        paiements/tests/test_services.py::test_initialiser_solde_idempotent)
        — rejouer ne crée donc jamais de second solde. Ce test-ci vérifie le
        comportement du relais dans ce scénario : aucune exception, un
        second appel gRPC part bien (la protection anti-doublon n'est pas de
        son ressort), l'événement retrouve son état ENVOYE."""
        self.paiement_client.initialiser_solde.return_value = True
        event = self._event()

        self.svc.relayer_lot()
        event.refresh_from_db()
        self.assertEqual(event.statut, StatutOutboxEvent.ENVOYE)

        # Simule un redémarrage du relais AVANT que le statut ENVOYE n'ait pu
        # être committé (l'appel gRPC, lui, avait déjà réussi et créé le solde
        # côté Paiement Service).
        event.statut = StatutOutboxEvent.EN_ATTENTE
        event.save(update_fields=["statut"])

        envoyes, echoues, abandonnes = self.svc.relayer_lot()

        self.assertEqual((envoyes, echoues, abandonnes), (1, 0, 0))
        self.assertEqual(self.paiement_client.initialiser_solde.call_count, 2)
        event.refresh_from_db()
        self.assertEqual(event.statut, StatutOutboxEvent.ENVOYE)


class OutboxSchedulerTests(TestCase):
    """factures/schedulers.py — même patron que reporting/stats/schedulers.py."""

    def tearDown(self) -> None:
        from factures import schedulers

        schedulers.stop_scheduler()
        schedulers._scheduler = None

    @patch.object(OutboxRelayService, "relayer_lot", return_value=(2, 1, 0))
    def test_outbox_relay_job_appelle_le_relais(self, mock_relayer: MagicMock) -> None:
        from factures.schedulers import outbox_relay_job

        outbox_relay_job()  # ne doit lever aucune exception (SQLite : verrou sauté)

        mock_relayer.assert_called_once()

    @patch.object(OutboxRelayService, "relayer_lot", side_effect=RuntimeError("boom"))
    def test_outbox_relay_job_ne_propage_pas_les_erreurs(self, mock_relayer: MagicMock) -> None:
        """Un job de fond ne doit jamais planter le process gRPC qui l'héberge."""
        from factures.schedulers import outbox_relay_job

        outbox_relay_job()  # ne doit pas lever, l'erreur est journalisée

    def test_start_scheduler_enregistre_le_job(self) -> None:
        from apscheduler.triggers.interval import IntervalTrigger

        from factures.schedulers import start_scheduler

        start_scheduler()

        from factures import schedulers

        assert schedulers._scheduler is not None
        job = schedulers._scheduler.get_job("outbox_relay")
        self.assertIsNotNone(job)
        self.assertIsInstance(job.trigger, IntervalTrigger)

    def test_start_scheduler_idempotent(self) -> None:
        from factures.schedulers import start_scheduler

        start_scheduler()
        start_scheduler()  # ne doit pas lever ni dupliquer le scheduler

        from factures import schedulers

        assert schedulers._scheduler is not None
        self.assertTrue(schedulers._scheduler.running)
