"""Tests du serveur gRPC du Campagne Service."""

import sys
from decimal import Decimal
from pathlib import Path
from types import SimpleNamespace
from typing import Any
from unittest.mock import MagicMock, patch

import grpc
from django.conf import settings
from django.core.exceptions import ObjectDoesNotExist, ValidationError
from django.test import TestCase

sys.path.insert(0, str(Path(settings.BASE_DIR) / "proto"))

import campagne_service_pb2 as pb

from campagnes.grpc_clients import AbonneServiceClient, FacturationServiceClient
from campagnes.grpc_server import CampagneServicer
from campagnes.models import StatutCampagne, StatutReleve
from campagnes.services import CampagneService

# Voir campagnes/tests/test_services.py : ajouter_abonne_campagne vérifie
# désormais le statut ACTIF de l'abonné (ANO-003) via un appel gRPC réel.
# `compteur` reflète la forme réelle d'un AbonneResponse (message protobuf
# toujours présent, jamais absent) — `_get_dernier_index` s'y replie pour tout
# abonné sans relevé, y compris dans les tests qui ne portent pas eux-mêmes
# sur ce repli.
_abonne_patcher = patch.object(
    AbonneServiceClient,
    "get_abonne",
    return_value=SimpleNamespace(statut="ACTIF", compteur=SimpleNamespace(index_initial=0.0)),
)

# CorrigerReleve interroge désormais Facturation Service pour savoir si une
# facture doit être régénérée (voir TestCorrigerReleveRegenerationFacture).
# Par défaut, aucune facture n'existe encore : évite qu'une centaine de tests
# sans rapport avec la facturation ne tentent chacun un appel réseau réel vers
# un Facturation Service absent en test.
_facture_active_patcher = patch.object(FacturationServiceClient, "get_facture_active", return_value=None)


def setUpModule() -> None:
    _abonne_patcher.start()
    _facture_active_patcher.start()


def tearDownModule() -> None:
    _abonne_patcher.stop()
    _facture_active_patcher.stop()


def _mock_context() -> MagicMock:
    # Le mapping exception -> abort est fait par l'interceptor (voir
    # test_grpc_interceptors.py) : les servicers propagent l'exception métier.
    return MagicMock(spec=grpc.ServicerContext)


class TestCreateCampagneRPC(TestCase):
    def setUp(self) -> None:
        self.servicer = CampagneServicer()

    def test_create_campagne_succes(self) -> None:
        request = pb.CreateCampagneRequest(
            nom="Campagne Juillet",
            periode_mois=7,
            periode_annee=2026,
            created_by="user-001",
        )
        response = self.servicer.CreateCampagne(request, _mock_context())
        self.assertIsNotNone(response.campagne_id)
        self.assertEqual(response.nom, "Campagne Juillet")
        self.assertEqual(response.statut, StatutCampagne.PLANIFIEE)

    def test_create_campagne_nom_vide_abort(self) -> None:
        request = pb.CreateCampagneRequest(nom="", periode_mois=1, periode_annee=2026, created_by="user-001")
        with self.assertRaises(ValidationError):
            self.servicer.CreateCampagne(request, _mock_context())

    def test_create_campagne_mois_invalide_abort(self) -> None:
        request = pb.CreateCampagneRequest(nom="X", periode_mois=0, periode_annee=2026, created_by="user-001")
        with self.assertRaises(ValidationError):
            self.servicer.CreateCampagne(request, _mock_context())


class TestGetCampagneRPC(TestCase):
    def setUp(self) -> None:
        self.servicer = CampagneServicer()
        self.campagne = CampagneService().creer_campagne(
            nom="Test", periode_mois=1, periode_annee=2026, created_by="user-001"
        )

    def test_get_campagne_succes(self) -> None:
        request = pb.CampagneIdRequest(campagne_id=str(self.campagne.id))
        response = self.servicer.GetCampagne(request, _mock_context())
        self.assertEqual(response.campagne_id, str(self.campagne.id))

    def test_get_campagne_inexistante_abort(self) -> None:
        request = pb.CampagneIdRequest(campagne_id="00000000-0000-0000-0000-000000000000")
        with self.assertRaises(ObjectDoesNotExist):
            self.servicer.GetCampagne(request, _mock_context())


class TestListCampagnesRPC(TestCase):
    def setUp(self) -> None:
        self.servicer = CampagneServicer()
        svc = CampagneService()
        self.c1 = svc.creer_campagne("C1", 1, 2026, created_by="user-A")
        svc.creer_campagne("C2", 2, 2026, created_by="user-B")

    def test_list_all_sans_filtre(self) -> None:
        request = pb.ListCampagnesRequest(created_by="")
        response = self.servicer.ListCampagnes(request, _mock_context())
        self.assertEqual(len(response.campagnes), 2)

    def test_list_filtre_created_by(self) -> None:
        request = pb.ListCampagnesRequest(created_by="user-A")
        response = self.servicer.ListCampagnes(request, _mock_context())
        self.assertEqual(len(response.campagnes), 1)
        self.assertEqual(response.campagnes[0].nom, "C1")

    def test_list_filtre_agent_id(self) -> None:
        self.servicer.AssignerAgent(
            pb.AssignerAgentRequest(campagne_id=str(self.c1.id), agent_id="agent-X"),
            _mock_context(),
        )
        request = pb.ListCampagnesRequest(agent_id="agent-X")
        response = self.servicer.ListCampagnes(request, _mock_context())
        self.assertEqual(len(response.campagnes), 1)
        self.assertEqual(response.campagnes[0].nom, "C1")

    def test_list_filtre_agent_id_sans_affectation_retourne_vide(self) -> None:
        request = pb.ListCampagnesRequest(agent_id="agent-inconnu")
        response = self.servicer.ListCampagnes(request, _mock_context())
        self.assertEqual(len(response.campagnes), 0)


class TestAssignerAgentRPC(TestCase):
    def setUp(self) -> None:
        self.servicer = CampagneServicer()
        svc = CampagneService()
        self.campagne = svc.creer_campagne("C1", 1, 2026, created_by="user-A")

    def test_assigner_agent_succes(self) -> None:
        request = pb.AssignerAgentRequest(campagne_id=str(self.campagne.id), agent_id="agent-001")
        response = self.servicer.AssignerAgent(request, _mock_context())
        self.assertEqual(response.campagne_id, str(self.campagne.id))

    def test_assigner_agent_idempotent(self) -> None:
        req = pb.AssignerAgentRequest(campagne_id=str(self.campagne.id), agent_id="agent-001")
        self.servicer.AssignerAgent(req, _mock_context())
        # deuxième appel ne doit pas lever d'exception
        response = self.servicer.AssignerAgent(req, _mock_context())
        self.assertEqual(response.campagne_id, str(self.campagne.id))

    def test_assigner_agent_campagne_inexistante_abort(self) -> None:
        request = pb.AssignerAgentRequest(campagne_id="00000000-0000-0000-0000-000000000000", agent_id="agent-001")
        with self.assertRaises(ObjectDoesNotExist):
            self.servicer.AssignerAgent(request, _mock_context())


class TestCloturerCampagneRPC(TestCase):
    def setUp(self) -> None:
        self.servicer = CampagneServicer()
        svc = CampagneService()
        campagne = svc.creer_campagne("C1", 1, 2026, created_by="user-A")
        svc.demarrer_campagne(str(campagne.id))
        self.campagne = campagne

    @patch(
        "campagnes.grpc_server.FacturationServiceClient.notifier_campagne_cloturee",
        return_value=True,
    )
    def test_cloturer_succes(self, mock_notif: MagicMock) -> None:
        request = pb.CampagneIdRequest(campagne_id=str(self.campagne.id))
        response = self.servicer.CloturerCampagne(request, _mock_context())
        self.assertEqual(response.statut, StatutCampagne.CLOTUREE)
        mock_notif.assert_called_once()

    @patch("campagnes.grpc_server.publish_reporting_event")
    @patch("campagnes.grpc_server.FacturationServiceClient.notifier_campagne_cloturee", return_value=True)
    def test_cloturer_publie_stats_reporting(self, mock_notif: MagicMock, mock_pub: MagicMock) -> None:
        request = pb.CampagneIdRequest(campagne_id=str(self.campagne.id))
        self.servicer.CloturerCampagne(request, _mock_context())

        mock_pub.assert_called_once()
        args, kwargs = mock_pub.call_args
        self.assertEqual(args[0], "CAMPAGNE_STATS")
        self.assertEqual(kwargs["campagne_id"], str(self.campagne.id))
        self.assertEqual(kwargs["nom_campagne"], "C1")

    def test_cloturer_campagne_planifiee_abort(self) -> None:
        svc = CampagneService()
        c2 = svc.creer_campagne("C2", 2, 2026, created_by="user-A")
        request = pb.CampagneIdRequest(campagne_id=str(c2.id))
        with self.assertRaises(ValidationError):
            self.servicer.CloturerCampagne(request, _mock_context())

    @patch("campagnes.grpc_server.FacturationServiceClient.notifier_campagne_cloturee", return_value=False)
    def test_cloturer_echec_facturation_pose_le_marqueur_en_attente(self, mock_notif: MagicMock) -> None:
        """Régression : un échec gRPC de Facturation Service à la clôture ne
        doit plus être perdu silencieusement — il doit rester visible et
        rattrapable via `facturation_en_attente`."""
        request = pb.CampagneIdRequest(campagne_id=str(self.campagne.id))
        response = self.servicer.CloturerCampagne(request, _mock_context())
        self.assertEqual(response.statut, StatutCampagne.CLOTUREE)  # la clôture n'est pas bloquée
        self.campagne.refresh_from_db()
        self.assertTrue(self.campagne.facturation_en_attente)

    @patch("campagnes.grpc_server.FacturationServiceClient.notifier_campagne_cloturee", return_value=True)
    def test_cloturer_succes_ne_pose_pas_le_marqueur(self, mock_notif: MagicMock) -> None:
        request = pb.CampagneIdRequest(campagne_id=str(self.campagne.id))
        self.servicer.CloturerCampagne(request, _mock_context())
        self.campagne.refresh_from_db()
        self.assertFalse(self.campagne.facturation_en_attente)

    @patch("campagnes.grpc_server.FacturationServiceClient.notifier_campagne_cloturee")
    def test_cloturer_generer_factures_auto_faux_ne_pose_pas_le_marqueur(self, mock_notif: MagicMock) -> None:
        """Une campagne configurée sans génération automatique n'appelle jamais
        Facturation Service — pas de marqueur en attente à poser."""
        svc = CampagneService()
        c2 = svc.creer_campagne("C2", 2, 2026, created_by="user-A", generer_factures_auto=False)
        svc.demarrer_campagne(str(c2.id))
        request = pb.CampagneIdRequest(campagne_id=str(c2.id))
        self.servicer.CloturerCampagne(request, _mock_context())
        mock_notif.assert_not_called()
        c2.refresh_from_db()
        self.assertFalse(c2.facturation_en_attente)


class TestDemarrerCampagneRPC(TestCase):
    def setUp(self) -> None:
        self.servicer = CampagneServicer()
        self.svc = CampagneService()

    def test_demarrer_planifiee_succes(self) -> None:
        campagne = self.svc.creer_campagne("C1", 1, 2026, created_by="user-A")  # PLANIFIEE
        request = pb.CampagneIdRequest(campagne_id=str(campagne.id))
        response = self.servicer.DemarrerCampagne(request, _mock_context())
        self.assertEqual(response.statut, StatutCampagne.EN_COURS)

    def test_demarrer_deja_en_cours_abort(self) -> None:
        campagne = self.svc.creer_campagne("C2", 2, 2026, created_by="user-A")
        self.svc.demarrer_campagne(str(campagne.id))  # déjà EN_COURS
        request = pb.CampagneIdRequest(campagne_id=str(campagne.id))
        with self.assertRaises(ValidationError):
            self.servicer.DemarrerCampagne(request, _mock_context())


class TestAjouterAbonnesCampagneRPC(TestCase):
    def setUp(self) -> None:
        self.servicer = CampagneServicer()
        self.svc = CampagneService()
        self.campagne = self.svc.creer_campagne("CA", 1, 2026, created_by="user-A")

    def test_ajouter_succes_alimente_la_progression(self) -> None:
        cid = str(self.campagne.id)
        req = pb.AjouterAbonnesCampagneRequest(campagne_id=cid, abonne_ids=["ab-1", "ab-2"])
        resp = self.servicer.AjouterAbonnesCampagne(req, _mock_context())
        self.assertEqual(resp.nb_ajoutes, 2)
        self.assertEqual(resp.nb_ignores, 0)
        # Le compteur « abonnés à relever » reflète désormais les 2 abonnés.
        prog = self.servicer.GetProgression(pb.CampagneIdRequest(campagne_id=cid), _mock_context())
        self.assertEqual(prog.total_abonnes, 2)
        self.assertEqual(prog.nb_en_attente, 2)

    def test_ajouter_ignore_les_doublons(self) -> None:
        cid = str(self.campagne.id)
        self.svc.ajouter_abonne_campagne(cid, "ab-1", ancien_index=Decimal("0"))  # déjà inscrit
        req = pb.AjouterAbonnesCampagneRequest(campagne_id=cid, abonne_ids=["ab-1", "ab-2"])
        resp = self.servicer.AjouterAbonnesCampagne(req, _mock_context())
        self.assertEqual(resp.nb_ajoutes, 1)  # ab-2 seulement
        self.assertEqual(resp.nb_ignores, 1)  # ab-1 déjà présent

    def test_ajouter_ignore_abonne_non_actif(self) -> None:
        cid = str(self.campagne.id)
        with patch.object(
            AbonneServiceClient,
            "get_abonne",
            return_value=SimpleNamespace(statut="SUSPENDU", compteur=SimpleNamespace(index_initial=0.0)),
        ):
            req = pb.AjouterAbonnesCampagneRequest(campagne_id=cid, abonne_ids=["ab-x"])
            resp = self.servicer.AjouterAbonnesCampagne(req, _mock_context())
        self.assertEqual(resp.nb_ajoutes, 0)
        self.assertEqual(resp.nb_ignores, 1)

    def test_ajouter_campagne_cloturee_abort(self) -> None:
        from campagnes.models import Campagne

        cid = str(self.campagne.id)
        Campagne.objects.filter(id=cid).update(statut=StatutCampagne.CLOTUREE)
        req = pb.AjouterAbonnesCampagneRequest(campagne_id=cid, abonne_ids=["ab-1"])
        with self.assertRaises(ValidationError):
            self.servicer.AjouterAbonnesCampagne(req, _mock_context())


class TestListRelevesTourneeRPC(TestCase):
    def setUp(self) -> None:
        from campagnes.models import Releve

        self.servicer = CampagneServicer()
        self.campagne = CampagneService().creer_campagne("CT", 1, 2026, created_by="user-A")
        c = self.campagne
        # 2 abonnés à relever, dans 2 zones distinctes
        Releve.objects.create(
            campagne=c, abonne_id="ab-z1", ancien_index=0, statut=StatutReleve.A_RELEVER, quartier="Q1", camp=1
        )
        Releve.objects.create(
            campagne=c, abonne_id="ab-z2", ancien_index=0, statut=StatutReleve.A_RELEVER, quartier="Q2", camp=2
        )
        # 1 relevé saisi par NOTRE agent (AGENT-1), 1 par un autre (AGENT-2)
        Releve.objects.create(
            campagne=c,
            abonne_id="ab-mine",
            ancien_index=0,
            nouveau_index=10,
            statut=StatutReleve.RELEVE,
            agent_id="AGENT-1",
            quartier="Q3",
            camp=3,
        )
        Releve.objects.create(
            campagne=c,
            abonne_id="ab-other",
            ancien_index=0,
            nouveau_index=5,
            statut=StatutReleve.RELEVE,
            agent_id="AGENT-2",
            quartier="Q1",
            camp=1,
        )

    def _tournee(self, agent_id: str) -> set[str]:
        req = pb.ListRelevesTourneeRequest(campagne_id=str(self.campagne.id), agent_id=agent_id)
        resp = self.servicer.ListRelevesTournee(req, _mock_context())
        return {r.abonne_id for r in resp.releves}

    def test_agent_global_voit_tous_les_a_relever_plus_ses_saisis(self) -> None:
        # AGENT-1 n'a AUCUNE zone → périmètre = toute la campagne
        abos = self._tournee("AGENT-1")
        self.assertEqual(abos, {"ab-z1", "ab-z2", "ab-mine"})
        self.assertNotIn("ab-other", abos)  # saisi par un autre agent → exclu

    def test_agent_avec_zone_voit_seulement_sa_zone(self) -> None:
        from campagnes.models import AffectationZone

        AffectationZone.objects.create(campagne=self.campagne, agent_id="AGENT-1", quartier="Q1", camp=1)
        abos = self._tournee("AGENT-1")
        self.assertIn("ab-z1", abos)  # A_RELEVER de sa zone Q1
        self.assertIn("ab-mine", abos)  # ses saisis, quelle que soit la zone
        self.assertNotIn("ab-z2", abos)  # A_RELEVER hors de sa zone → exclu


class TestSaisirIndexRPC(TestCase):
    def setUp(self) -> None:
        self.servicer = CampagneServicer()
        svc = CampagneService()
        campagne = svc.creer_campagne("C1", 1, 2026, created_by="user-A")
        svc.demarrer_campagne(str(campagne.id))
        svc.ajouter_abonne_campagne(str(campagne.id), "abonne-001", ancien_index=Decimal("100"))
        self.campagne = campagne

    def test_saisir_index_succes(self) -> None:
        request = pb.SaisirIndexRequest(
            campagne_id=str(self.campagne.id),
            abonne_id="abonne-001",
            nouveau_index=150.0,
            agent_id="agent-001",
        )
        response = self.servicer.SaisirIndex(request, _mock_context())
        self.assertEqual(response.statut, StatutReleve.RELEVE)
        self.assertAlmostEqual(response.consommation, 50.0)

    def test_saisir_index_cree_releve_si_absent(self) -> None:
        request = pb.SaisirIndexRequest(
            campagne_id=str(self.campagne.id),
            abonne_id="abonne-nouveau",
            nouveau_index=200.0,
            agent_id="agent-001",
        )
        response = self.servicer.SaisirIndex(request, _mock_context())
        self.assertEqual(response.statut, StatutReleve.RELEVE)

    def test_saisir_index_abonne_suspendu_abort(self) -> None:
        """Régression ANO-003 : la création à la volée du relevé (abonné pas
        encore présent dans la campagne) doit refuser un abonné non ACTIF."""
        request = pb.SaisirIndexRequest(
            campagne_id=str(self.campagne.id),
            abonne_id="abonne-suspendu",
            nouveau_index=200.0,
            agent_id="agent-001",
        )
        with patch.object(
            AbonneServiceClient,
            "get_abonne",
            return_value=SimpleNamespace(statut="SUSPENDU", compteur=SimpleNamespace(index_initial=0.0)),
        ):
            with self.assertRaises(ValidationError):
                self.servicer.SaisirIndex(request, _mock_context())

    def test_saisir_index_inferieur_abort(self) -> None:
        request = pb.SaisirIndexRequest(
            campagne_id=str(self.campagne.id),
            abonne_id="abonne-001",
            nouveau_index=50.0,
            agent_id="agent-001",
        )
        with self.assertRaises(ValidationError):
            self.servicer.SaisirIndex(request, _mock_context())


class TestCorrigerReleveRPC(TestCase):
    def setUp(self) -> None:
        self.servicer = CampagneServicer()
        svc = CampagneService()
        campagne = svc.creer_campagne("C1", 1, 2026, created_by="user-A")
        svc.demarrer_campagne(str(campagne.id))
        svc.ajouter_abonne_campagne(str(campagne.id), "abonne-001", ancien_index=Decimal("100"))
        # Saisie initiale (par un agent) avant toute correction.
        self.servicer.SaisirIndex(
            pb.SaisirIndexRequest(
                campagne_id=str(campagne.id),
                abonne_id="abonne-001",
                nouveau_index=150.0,
                agent_id="agent-001",
                auteur_username="bob",
                auteur_role="AGENT",
            ),
            _mock_context(),
        )
        self.campagne = campagne

    def _corriger_request(self, **kw: Any) -> pb.CorrigerReleveRequest:
        defaults = dict(
            campagne_id=str(self.campagne.id),
            abonne_id="abonne-001",
            nouveau_index=180.0,
            auteur_id="admin-001",
            auteur_username="alice",
            auteur_role="ADMIN",
        )
        return pb.CorrigerReleveRequest(**{**defaults, **kw})

    def test_corriger_releve_succes_expose_audit_et_agent(self) -> None:
        response = self.servicer.CorrigerReleve(self._corriger_request(), _mock_context())
        self.assertEqual(response.nouveau_index, 180.0)
        self.assertEqual(response.consommation, 80.0)
        # agent_id d'origine préservé, journal SAISIE + CORRECTION exposé.
        self.assertEqual(response.agent_id, "agent-001")
        self.assertEqual([a.action for a in response.audit], ["SAISIE", "CORRECTION"])
        self.assertEqual(response.audit[1].auteur_id, "admin-001")

    def test_corriger_releve_apres_cloture(self) -> None:
        from campagnes.repositories import CampagneRepository

        CampagneRepository().update_statut(self.campagne, StatutCampagne.CLOTUREE)
        response = self.servicer.CorrigerReleve(self._corriger_request(nouveau_index=175.0), _mock_context())
        self.assertEqual(response.nouveau_index, 175.0)

    def test_corriger_releve_introuvable_abort(self) -> None:
        with self.assertRaises(ObjectDoesNotExist):
            self.servicer.CorrigerReleve(self._corriger_request(abonne_id="inconnu"), _mock_context())


class TestCorrigerReleveRegenerationFactureRPC(TestCase):
    """Régression : une correction de relevé postérieure à la facturation doit
    répercuter la correction sur la facture déjà émise (voir services.py::
    CampagneService.regenerer_facture_si_necessaire)."""

    def setUp(self) -> None:
        from campagnes.repositories import CampagneRepository
        from campagnes.models import RegenerationFactureEnAttente

        self.servicer = CampagneServicer()
        svc = CampagneService()
        campagne = svc.creer_campagne("C1", 1, 2026, created_by="user-A")
        svc.demarrer_campagne(str(campagne.id))
        svc.ajouter_abonne_campagne(str(campagne.id), "abonne-001", ancien_index=Decimal("100"))
        self.servicer.SaisirIndex(
            pb.SaisirIndexRequest(
                campagne_id=str(campagne.id),
                abonne_id="abonne-001",
                nouveau_index=150.0,
                agent_id="agent-001",
                auteur_username="bob",
                auteur_role="AGENT",
            ),
            _mock_context(),
        )
        CampagneRepository().update_statut(campagne, StatutCampagne.CLOTUREE)
        self.campagne = campagne
        self._RegenerationFactureEnAttente = RegenerationFactureEnAttente

    def _corriger_request(self, **kw: Any) -> pb.CorrigerReleveRequest:
        defaults = dict(
            campagne_id=str(self.campagne.id),
            abonne_id="abonne-001",
            nouveau_index=180.0,
            auteur_id="admin-001",
            auteur_username="alice",
            auteur_role="ADMIN",
        )
        return pb.CorrigerReleveRequest(**{**defaults, **kw})

    def test_sans_facture_existante_ne_declenche_rien(self) -> None:
        """`get_facture_active` renvoie None par défaut (patch de module) :
        aucune facture n'existe encore, la correction est un no-op côté
        facturation."""
        with patch("campagnes.grpc_clients.FacturationServiceClient.regenerer_facture") as mock_regen:
            self.servicer.CorrigerReleve(self._corriger_request(), _mock_context())
            mock_regen.assert_not_called()
        self.assertFalse(self._RegenerationFactureEnAttente.objects.exists())

    @patch("campagnes.grpc_clients.FacturationServiceClient.regenerer_facture", return_value=True)
    @patch("campagnes.grpc_clients.FacturationServiceClient.get_facture_active", return_value="facture-001")
    def test_avec_facture_existante_declenche_la_regeneration(
        self, mock_get_active: MagicMock, mock_regen: MagicMock
    ) -> None:
        self.servicer.CorrigerReleve(self._corriger_request(), _mock_context())

        mock_get_active.assert_called_once_with(str(self.campagne.id), "abonne-001")
        mock_regen.assert_called_once()
        args, kwargs = mock_regen.call_args
        self.assertEqual(args[0], "facture-001")
        self.assertEqual(kwargs["regenere_par"], "admin-001")
        self.assertIn("alice", kwargs["motif"])  # username privilégié sur l'id dans le motif affiché
        # Résolu du premier coup : rien en attente de retry.
        self.assertFalse(self._RegenerationFactureEnAttente.objects.exists())

    @patch("campagnes.grpc_clients.FacturationServiceClient.get_facture_active", side_effect=grpc.RpcError("down"))
    def test_facturation_indisponible_ne_perd_pas_la_correction(self, mock_get_active: MagicMock) -> None:
        """Facturation Service injoignable au moment de la correction : la
        correction elle-même doit tout de même réussir (dégradation propre),
        et une entrée de retry doit être posée pour rattraper la répercussion
        sur la facture plus tard."""
        response = self.servicer.CorrigerReleve(self._corriger_request(), _mock_context())

        self.assertEqual(response.nouveau_index, 180.0)  # la correction est bien passée
        entree = self._RegenerationFactureEnAttente.objects.get(campagne=self.campagne, abonne_id="abonne-001")
        self.assertIn("alice", entree.motif)
        self.assertEqual(entree.demande_par, "admin-001")

    @patch("campagnes.grpc_clients.FacturationServiceClient.regenerer_facture", return_value=False)
    @patch("campagnes.grpc_clients.FacturationServiceClient.get_facture_active", return_value="facture-001")
    def test_echec_de_regeneration_pose_une_entree_de_retry(
        self, mock_get_active: MagicMock, mock_regen: MagicMock
    ) -> None:
        self.servicer.CorrigerReleve(self._corriger_request(), _mock_context())

        self.assertTrue(
            self._RegenerationFactureEnAttente.objects.filter(campagne=self.campagne, abonne_id="abonne-001").exists()
        )


class TestGetProgressionRPC(TestCase):
    def setUp(self) -> None:
        self.servicer = CampagneServicer()
        svc = CampagneService()
        campagne = svc.creer_campagne("C1", 1, 2026, created_by="user-A")
        svc.demarrer_campagne(str(campagne.id))
        svc.ajouter_abonne_campagne(str(campagne.id), "abonne-001", Decimal("100"))
        svc.ajouter_abonne_campagne(str(campagne.id), "abonne-002", Decimal("200"))
        self.campagne = campagne

    def test_progression_2_a_relever(self) -> None:
        request = pb.CampagneIdRequest(campagne_id=str(self.campagne.id))
        response = self.servicer.GetProgression(request, _mock_context())
        self.assertEqual(response.total_abonnes, 2)
        self.assertEqual(response.nb_releves, 0)
        self.assertEqual(response.nb_en_attente, 2)
        self.assertAlmostEqual(response.pourcentage, 0.0)


class TestGetResumeClotureRPC(TestCase):
    def setUp(self) -> None:
        from campagnes.models import StatutReleve

        self.servicer = CampagneServicer()
        svc = CampagneService()
        campagne = svc.creer_campagne("C1", 1, 2026, created_by="user-A")
        svc.demarrer_campagne(str(campagne.id))
        # 2 relevés, 1 estimé, 1 restant (A_RELEVER)
        statuts = [StatutReleve.RELEVE, StatutReleve.RELEVE, StatutReleve.ESTIME, StatutReleve.A_RELEVER]
        for i, s in enumerate(statuts):
            r = svc.ajouter_abonne_campagne(str(campagne.id), f"abonne-{i:03d}", Decimal("0"))
            r.statut = s
            r.save()
        self.campagne = campagne

    def test_resume_cloture(self) -> None:
        request = pb.CampagneIdRequest(campagne_id=str(self.campagne.id))
        response = self.servicer.GetResumeCloture(request, _mock_context())
        self.assertEqual(response.total_abonnes, 4)
        self.assertEqual(response.nb_releves, 2)
        self.assertEqual(response.nb_estimes, 1)
        self.assertEqual(response.nb_restants, 1)
        self.assertEqual(response.nb_factures_a_generer, 3)


class TestMarquerNonReleveRPC(TestCase):
    def setUp(self) -> None:
        self.servicer = CampagneServicer()
        svc = CampagneService()
        campagne = svc.creer_campagne("C1", 1, 2026, created_by="user-A")
        svc.demarrer_campagne(str(campagne.id))
        svc.ajouter_abonne_campagne(str(campagne.id), "abonne-001", Decimal("100"))
        self.campagne = campagne

    def test_marquer_non_releve_succes(self) -> None:
        request = pb.MarquerNonReleveRequest(
            campagne_id=str(self.campagne.id),
            abonne_id="abonne-001",
            statut="NON_RELEVE",
            observation="Absent",
        )
        response = self.servicer.MarquerNonReleve(request, _mock_context())
        self.assertEqual(response.statut, StatutReleve.NON_RELEVE)

    def test_marquer_estime_succes(self) -> None:
        request = pb.MarquerNonReleveRequest(
            campagne_id=str(self.campagne.id),
            abonne_id="abonne-001",
            statut="ESTIME",
            observation="Compteur illisible",
        )
        response = self.servicer.MarquerNonReleve(request, _mock_context())
        self.assertEqual(response.statut, StatutReleve.ESTIME)

    def test_marquer_statut_invalide_abort(self) -> None:
        request = pb.MarquerNonReleveRequest(
            campagne_id=str(self.campagne.id),
            abonne_id="abonne-001",
            statut="RELEVE",
        )
        with self.assertRaises(ValidationError):
            self.servicer.MarquerNonReleve(request, _mock_context())

    def test_marquer_releve_absent_abort(self) -> None:
        request = pb.MarquerNonReleveRequest(
            campagne_id=str(self.campagne.id),
            abonne_id="abonne-inconnu",
            statut="NON_RELEVE",
        )
        with self.assertRaises(ObjectDoesNotExist):
            self.servicer.MarquerNonReleve(request, _mock_context())


class TestGetReleveRPC(TestCase):
    def setUp(self) -> None:
        self.servicer = CampagneServicer()
        svc = CampagneService()
        campagne = svc.creer_campagne("C1", 1, 2026, created_by="user-A")
        svc.demarrer_campagne(str(campagne.id))
        releve = svc.ajouter_abonne_campagne(str(campagne.id), "abonne-001", Decimal("100"))
        from campagnes.services import ReleveService

        ReleveService().saisir_index(str(releve.id), nouveau_index=Decimal("150"), agent_id="agent-001")
        self.releve_id = str(releve.id)

    def test_get_releve_succes(self) -> None:
        request = pb.ReleveIdRequest(releve_id=self.releve_id)
        response = self.servicer.GetReleve(request, _mock_context())
        self.assertEqual(response.releve_id, self.releve_id)
        self.assertEqual(response.statut, StatutReleve.RELEVE)
        self.assertAlmostEqual(response.consommation, 50.0)

    def test_get_releve_inexistant_abort(self) -> None:
        request = pb.ReleveIdRequest(releve_id="00000000-0000-0000-0000-000000000000")
        with self.assertRaises(ObjectDoesNotExist):
            self.servicer.GetReleve(request, _mock_context())


class TestListRelevesRPC(TestCase):
    def setUp(self) -> None:
        self.servicer = CampagneServicer()
        svc = CampagneService()
        campagne = svc.creer_campagne("C1", 1, 2026, created_by="user-A")
        svc.demarrer_campagne(str(campagne.id))
        svc.ajouter_abonne_campagne(str(campagne.id), "abonne-001", Decimal("100"))
        svc.ajouter_abonne_campagne(str(campagne.id), "abonne-002", Decimal("200"))
        self.campagne = campagne

    def test_list_releves_retourne_tous(self) -> None:
        request = pb.CampagneIdRequest(campagne_id=str(self.campagne.id))
        response = self.servicer.ListReleves(request, _mock_context())
        self.assertEqual(len(response.releves), 2)

    def test_list_releves_campagne_inexistante_abort(self) -> None:
        request = pb.CampagneIdRequest(campagne_id="00000000-0000-0000-0000-000000000000")
        with self.assertRaises(ObjectDoesNotExist):
            self.servicer.ListReleves(request, _mock_context())


class TestGetDernierIndexRPC(TestCase):
    def setUp(self) -> None:
        self.servicer = CampagneServicer()
        svc = CampagneService()
        campagne = svc.creer_campagne("C1", 1, 2026, created_by="user-A")
        svc.demarrer_campagne(str(campagne.id))
        svc.ajouter_abonne_campagne(str(campagne.id), "abonne-001", Decimal("100"))
        self.campagne = campagne
        self.svc = svc

    def test_dernier_index_sans_releve_replie_sur_index_initial_du_compteur(self) -> None:
        """Un compteur neuf, jamais relevé, n'a pas 0.0 pour dernier index connu.

        Rendre 0.0 en dur ici — vrai seulement par coïncidence — faisait
        toujours échouer le remplacement d'un tel compteur : le service Abonné
        rejette un index de fermeture inférieur à l'index initial, non nul
        dès qu'un compteur est posé avec un index de départ."""
        with patch.object(
            AbonneServiceClient,
            "get_abonne",
            return_value=SimpleNamespace(statut="ACTIF", compteur=SimpleNamespace(index_initial=48.0)),
        ):
            request = pb.AbonneIdRequest(abonne_id="abonne-001")
            response = self.servicer.GetDernierIndex(request, _mock_context())
        self.assertAlmostEqual(response.dernier_index, 48.0)
        self.assertTrue(response.est_index_initial)

    def test_dernier_index_sans_releve_et_abonne_service_injoignable_replie_sur_zero(self) -> None:
        with patch.object(AbonneServiceClient, "get_abonne", side_effect=grpc.RpcError("indisponible")):
            request = pb.AbonneIdRequest(abonne_id="abonne-001")
            response = self.servicer.GetDernierIndex(request, _mock_context())
        self.assertAlmostEqual(response.dernier_index, 0.0)
        self.assertTrue(response.est_index_initial)

    def test_dernier_index_avec_releve_retourne_index(self) -> None:
        from campagnes.services import ReleveService

        ReleveService().saisir_index(
            str(self.svc.ajouter_abonne_campagne(str(self.campagne.id), "abonne-002", Decimal("50")).id),
            nouveau_index=Decimal("120"),
            agent_id="agent-001",
        )
        request = pb.AbonneIdRequest(abonne_id="abonne-002")
        response = self.servicer.GetDernierIndex(request, _mock_context())
        self.assertAlmostEqual(response.dernier_index, 120.0)
        self.assertFalse(response.est_index_initial)


class TestAffecterZonesRPC(TestCase):
    def setUp(self) -> None:
        self.servicer = CampagneServicer()
        svc = CampagneService()
        campagne = svc.creer_campagne("C1", 1, 2026, created_by="user-A")
        svc.demarrer_campagne(str(campagne.id))
        self.campagne = campagne

    def _affecter(self, agent_id: str, zones: list[tuple[str, int]]) -> pb.ListAgentsCampagneResponse:
        request = pb.AffecterZonesRequest(
            campagne_id=str(self.campagne.id),
            agent_id=agent_id,
            zones=[pb.Zone(quartier=q, camp=c) for q, c in zones],
        )
        return self.servicer.AffecterZones(request, _mock_context())

    def test_affecter_zones_retourne_agent_avec_zones(self) -> None:
        response = self._affecter("agent-1", [("Plateau", 3), ("Centre", 1)])
        agent = next(a for a in response.agents if a.agent_id == "agent-1")
        zones = {(z.quartier, z.camp) for z in agent.zones}
        self.assertEqual(zones, {("Plateau", 3), ("Centre", 1)})

    def test_list_agents_campagne_rpc(self) -> None:
        self._affecter("agent-1", [("Plateau", 3)])
        response = self.servicer.ListAgentsCampagne(
            pb.CampagneIdRequest(campagne_id=str(self.campagne.id)), _mock_context()
        )
        agent_ids = {a.agent_id for a in response.agents}
        self.assertIn("agent-1", agent_ids)

    def test_affecter_zones_campagne_introuvable_abort(self) -> None:
        request = pb.AffecterZonesRequest(
            campagne_id="00000000-0000-0000-0000-000000000000",
            agent_id="agent-1",
            zones=[pb.Zone(quartier="Plateau", camp=3)],
        )
        with self.assertRaises(ObjectDoesNotExist):
            self.servicer.AffecterZones(request, _mock_context())
