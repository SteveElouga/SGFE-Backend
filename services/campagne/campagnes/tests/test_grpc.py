"""Tests du serveur gRPC du Campagne Service."""

import sys
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import MagicMock, patch

import grpc
from django.conf import settings
from django.core.exceptions import ObjectDoesNotExist, ValidationError
from django.test import TestCase

sys.path.insert(0, str(Path(settings.BASE_DIR) / "proto"))

import campagne_service_pb2 as pb

from campagnes.grpc_clients import AbonneServiceClient
from campagnes.grpc_server import CampagneServicer
from campagnes.models import StatutCampagne, StatutReleve
from campagnes.services import CampagneService

# Voir campagnes/tests/test_services.py : ajouter_abonne_campagne vérifie
# désormais le statut ACTIF de l'abonné (ANO-003) via un appel gRPC réel.
_abonne_patcher = patch.object(AbonneServiceClient, "get_abonne", return_value=SimpleNamespace(statut="ACTIF"))


def setUpModule() -> None:
    _abonne_patcher.start()


def tearDownModule() -> None:
    _abonne_patcher.stop()


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
    def test_cloturer_succes(self, mock_notif) -> None:
        request = pb.CampagneIdRequest(campagne_id=str(self.campagne.id))
        response = self.servicer.CloturerCampagne(request, _mock_context())
        self.assertEqual(response.statut, StatutCampagne.CLOTUREE)
        mock_notif.assert_called_once()

    @patch("campagnes.grpc_server.publish_reporting_event")
    @patch("campagnes.grpc_server.FacturationServiceClient.notifier_campagne_cloturee", return_value=True)
    def test_cloturer_publie_stats_reporting(self, mock_notif, mock_pub) -> None:
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


class TestSaisirIndexRPC(TestCase):
    def setUp(self) -> None:
        self.servicer = CampagneServicer()
        svc = CampagneService()
        campagne = svc.creer_campagne("C1", 1, 2026, created_by="user-A")
        svc.demarrer_campagne(str(campagne.id))
        svc.ajouter_abonne_campagne(str(campagne.id), "abonne-001", ancien_index=100.0)
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
        with patch.object(AbonneServiceClient, "get_abonne", return_value=SimpleNamespace(statut="SUSPENDU")):
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
        svc.ajouter_abonne_campagne(str(campagne.id), "abonne-001", ancien_index=100.0)
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

    def _corriger_request(self, **kw) -> pb.CorrigerReleveRequest:
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


class TestGetProgressionRPC(TestCase):
    def setUp(self) -> None:
        self.servicer = CampagneServicer()
        svc = CampagneService()
        campagne = svc.creer_campagne("C1", 1, 2026, created_by="user-A")
        svc.demarrer_campagne(str(campagne.id))
        svc.ajouter_abonne_campagne(str(campagne.id), "abonne-001", 100.0)
        svc.ajouter_abonne_campagne(str(campagne.id), "abonne-002", 200.0)
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
            r = svc.ajouter_abonne_campagne(str(campagne.id), f"abonne-{i:03d}", 0.0)
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
        svc.ajouter_abonne_campagne(str(campagne.id), "abonne-001", 100.0)
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
        releve = svc.ajouter_abonne_campagne(str(campagne.id), "abonne-001", 100.0)
        from campagnes.services import ReleveService

        ReleveService().saisir_index(str(releve.id), nouveau_index=150.0, agent_id="agent-001")
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
        svc.ajouter_abonne_campagne(str(campagne.id), "abonne-001", 100.0)
        svc.ajouter_abonne_campagne(str(campagne.id), "abonne-002", 200.0)
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
        svc.ajouter_abonne_campagne(str(campagne.id), "abonne-001", 100.0)
        self.campagne = campagne
        self.svc = svc

    def test_dernier_index_sans_releve_retourne_zero(self) -> None:
        request = pb.AbonneIdRequest(abonne_id="abonne-001")
        response = self.servicer.GetDernierIndex(request, _mock_context())
        self.assertAlmostEqual(response.dernier_index, 0.0)
        self.assertTrue(response.est_index_initial)

    def test_dernier_index_avec_releve_retourne_index(self) -> None:
        from campagnes.services import ReleveService

        ReleveService().saisir_index(
            str(self.svc.ajouter_abonne_campagne(str(self.campagne.id), "abonne-002", 50.0).id),
            nouveau_index=120.0,
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

    def _affecter(self, agent_id: str, zones: list[tuple[str, int]]):
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
