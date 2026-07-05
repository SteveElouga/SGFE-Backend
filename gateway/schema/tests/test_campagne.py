"""Tests des resolvers GraphQL du Campagne Service (gateway)."""

from unittest.mock import MagicMock, patch

from django.test import SimpleTestCase

from proto import campagne_service_pb2 as campagne_pb
from schema.campagne_mutations import CampagneMutations
from schema.campagne_queries import CampagneQueries


def _campagne_response(**kwargs) -> campagne_pb.CampagneResponse:
    defaults = dict(
        campagne_id="camp-001",
        nom="Campagne Juillet",
        periode_mois=7,
        periode_annee=2026,
        statut="PLANIFIEE",
        date_planifiee="",
        date_creation="2026-07-01T00:00:00+00:00",
        date_cloture="",
        numero_mobile_money="",
        generer_factures_auto=True,
        envoyer_whatsapp_auto=True,
    )
    return campagne_pb.CampagneResponse(**{**defaults, **kwargs})


def _releve_response(**kwargs) -> campagne_pb.ReleveResponse:
    defaults = dict(
        releve_id="releve-001",
        abonne_id="abonne-001",
        ancien_index=100.0,
        nouveau_index=150.0,
        consommation=50.0,
        date_releve="2026-07-15T10:00:00+00:00",
        observation="",
        statut="RELEVE",
    )
    return campagne_pb.ReleveResponse(**{**defaults, **kwargs})


class TestCampagneQueries(SimpleTestCase):
    @patch("schema.campagne_queries.campagne_client")
    @patch("schema.campagne_queries.require_auth")
    @patch("schema.campagne_queries.require_role")
    def test_campagne_succes(self, mock_role, mock_auth, mock_client) -> None:
        mock_auth.return_value = MagicMock(role="ADMIN", user_id="user-001")
        mock_client.get_campagne.return_value = _campagne_response()
        info = MagicMock()
        result = CampagneQueries().campagne(info, campagne_id="camp-001")
        self.assertEqual(result.campagne_id, "camp-001")
        self.assertEqual(result.nom, "Campagne Juillet")

    @patch("schema.campagne_queries.campagne_client")
    @patch("schema.campagne_queries.require_auth")
    @patch("schema.campagne_queries.require_role")
    def test_campagnes_admin_sans_filtre(self, mock_role, mock_auth, mock_client) -> None:
        mock_auth.return_value = MagicMock(role="ADMIN", user_id="user-001")
        mock_client.list_campagnes.return_value = MagicMock(
            campagnes=[_campagne_response(campagne_id="c1"), _campagne_response(campagne_id="c2")]
        )
        info = MagicMock()
        result = CampagneQueries().campagnes(info)
        mock_client.list_campagnes.assert_called_once_with(created_by="", agent_id="")
        self.assertEqual(len(result), 2)

    @patch("schema.campagne_queries.campagne_client")
    @patch("schema.campagne_queries.require_auth")
    @patch("schema.campagne_queries.require_role")
    def test_campagnes_superviseur_avec_filtre(self, mock_role, mock_auth, mock_client) -> None:
        mock_auth.return_value = MagicMock(role="SUPERVISEUR", user_id="sup-001")
        mock_client.list_campagnes.return_value = MagicMock(campagnes=[_campagne_response()])
        info = MagicMock()
        CampagneQueries().campagnes(info)
        mock_client.list_campagnes.assert_called_once_with(created_by="sup-001", agent_id="")

    @patch("schema.campagne_queries.campagne_client")
    @patch("schema.campagne_queries.require_auth")
    @patch("schema.campagne_queries.require_role")
    def test_campagnes_agent_filtre_par_affectation(self, mock_role, mock_auth, mock_client) -> None:
        mock_auth.return_value = MagicMock(role="AGENT", user_id="agent-001")
        mock_client.list_campagnes.return_value = MagicMock(campagnes=[_campagne_response()])
        info = MagicMock()
        CampagneQueries().campagnes(info)
        mock_client.list_campagnes.assert_called_once_with(created_by="", agent_id="agent-001")

    @patch("schema.campagne_queries.campagne_client")
    @patch("schema.campagne_queries.require_auth")
    @patch("schema.campagne_queries.require_role")
    def test_progression(self, mock_role, mock_auth, mock_client) -> None:
        mock_auth.return_value = MagicMock(role="ADMIN", user_id="user-001")
        mock_client.get_progression.return_value = MagicMock(
            campagne_id="camp-001",
            total_abonnes=10,
            nb_releves=7,
            nb_en_attente=3,
            pourcentage=70.0,
        )
        info = MagicMock()
        result = CampagneQueries().progression(info, campagne_id="camp-001")
        self.assertEqual(result.total_abonnes, 10)
        self.assertAlmostEqual(result.pourcentage, 70.0)

    @patch("schema.campagne_queries.campagne_client")
    @patch("schema.campagne_queries.require_auth")
    @patch("schema.campagne_queries.require_role")
    def test_resume_cloture(self, mock_role, mock_auth, mock_client) -> None:
        mock_auth.return_value = MagicMock(role="ADMIN", user_id="user-001")
        mock_client.get_resume_cloture.return_value = MagicMock(
            campagne_id="camp-001",
            total_abonnes=50,
            nb_releves=40,
            nb_estimes=2,
            nb_non_releves=2,
            nb_restants=6,
            nb_factures_a_generer=42,
        )
        info = MagicMock()
        result = CampagneQueries().resume_cloture(info, campagne_id="camp-001")
        self.assertEqual(result.nb_factures_a_generer, 42)
        self.assertEqual(result.nb_estimes, 2)
        self.assertEqual(result.nb_restants, 6)
        mock_role.assert_called_once_with(info, "ADMIN", "SUPERVISEUR")

    @patch("schema.campagne_queries.campagne_client")
    @patch("schema.campagne_queries.require_auth")
    @patch("schema.campagne_queries.require_role")
    def test_dernier_index(self, mock_role, mock_auth, mock_client) -> None:
        mock_auth.return_value = MagicMock(role="AGENT", user_id="agent-001")
        mock_client.get_dernier_index.return_value = MagicMock(
            abonne_id="abonne-001",
            dernier_index=120.0,
            est_index_initial=False,
        )
        info = MagicMock()
        result = CampagneQueries().dernier_index(info, abonne_id="abonne-001")
        self.assertAlmostEqual(result.dernier_index, 120.0)
        self.assertFalse(result.est_index_initial)

    @patch("schema.campagne_queries.campagne_client")
    @patch("schema.campagne_queries.require_auth")
    @patch("schema.campagne_queries.require_role")
    def test_releves_par_agent_filtre(self, mock_role, mock_auth, mock_client) -> None:
        mock_auth.return_value = MagicMock(role="ADMIN", user_id="admin-001")
        mock_client.list_releves.return_value = MagicMock(
            releves=[
                _releve_response(releve_id="r1", agent_id="agent-001"),
                _releve_response(releve_id="r2", agent_id="agent-002"),
                _releve_response(releve_id="r3", agent_id="agent-001"),
            ]
        )
        info = MagicMock()
        result = CampagneQueries().releves_par_agent(info, campagne_id="camp-001", agent_id="agent-001")
        self.assertEqual([r.releve_id for r in result], ["r1", "r3"])

    @patch("schema.campagne_queries.campagne_client")
    @patch("schema.campagne_queries.require_auth")
    @patch("schema.campagne_queries.require_role")
    def test_releves_par_agent_agent_autre_refuse(self, mock_role, mock_auth, mock_client) -> None:
        mock_auth.return_value = MagicMock(role="AGENT", user_id="agent-001")
        mock_client.list_campagnes.return_value = MagicMock(campagnes=[MagicMock(campagne_id="camp-001")])
        info = MagicMock()
        with self.assertRaises(PermissionError):
            CampagneQueries().releves_par_agent(info, campagne_id="camp-001", agent_id="agent-999")


class TestCampagneMutations(SimpleTestCase):
    @patch("schema.campagne_mutations.campagne_client")
    @patch("schema.campagne_mutations.require_auth")
    @patch("schema.campagne_mutations.require_role")
    def test_creer_campagne_admin(self, mock_role, mock_auth, mock_client) -> None:
        from schema.campagne_types import CreateCampagneInput

        mock_auth.return_value = MagicMock(role="ADMIN", user_id="user-001")
        mock_client.create_campagne.return_value = _campagne_response()
        info = MagicMock()
        input_data = CreateCampagneInput(nom="Campagne Juillet", periode_mois=7, periode_annee=2026)
        result = CampagneMutations().creer_campagne(info, input=input_data)
        self.assertEqual(result.campagne_id, "camp-001")
        mock_client.create_campagne.assert_called_once_with(
            nom="Campagne Juillet",
            periode_mois=7,
            periode_annee=2026,
            date_planifiee="",
            created_by="user-001",
            numero_mobile_money="",
            generer_factures_auto=True,
            envoyer_whatsapp_auto=True,
            demarrer_maintenant=False,
        )

    @patch("schema.campagne_mutations.campagne_client")
    @patch("schema.campagne_mutations.require_auth")
    @patch("schema.campagne_mutations.require_role")
    def test_cloturer_campagne(self, mock_role, mock_auth, mock_client) -> None:
        mock_auth.return_value = MagicMock(role="ADMIN", user_id="user-001")
        mock_client.cloturer_campagne.return_value = _campagne_response(statut="CLOTUREE")
        info = MagicMock()
        result = CampagneMutations().cloturer_campagne(info, campagne_id="camp-001")
        self.assertEqual(result.statut, "CLOTUREE")

    @patch("schema.campagne_mutations.campagne_client")
    @patch("schema.campagne_mutations.require_auth")
    @patch("schema.campagne_mutations.require_role")
    def test_affecter_agent_admin(self, mock_role, mock_auth, mock_client) -> None:
        mock_auth.return_value = MagicMock(role="ADMIN", user_id="admin-001")
        mock_client.assigner_agent.return_value = _campagne_response()
        info = MagicMock()
        result = CampagneMutations().affecter_agent(info, campagne_id="camp-001", agent_id="agent-001")
        self.assertEqual(result.campagne_id, "camp-001")
        mock_client.assigner_agent.assert_called_once_with(campagne_id="camp-001", agent_id="agent-001")

    @patch("schema.campagne_queries.campagne_client")
    @patch("schema.campagne_mutations.campagne_client")
    @patch("schema.campagne_mutations.require_auth")
    @patch("schema.campagne_mutations.require_role")
    def test_saisir_index_agent(self, mock_role, mock_auth, mock_mut_client, mock_query_client) -> None:
        from schema.campagne_types import SaisirIndexInput

        mock_auth.return_value = MagicMock(role="AGENT", user_id="agent-001", username="bob")
        mock_mut_client.saisir_index.return_value = _releve_response()
        # _verifier_acces_campagne appelle list_campagnes via campagne_queries.campagne_client
        mock_query_client.list_campagnes.return_value = MagicMock(campagnes=[MagicMock(campagne_id="camp-001")])
        info = MagicMock()
        input_data = SaisirIndexInput(campagne_id="camp-001", abonne_id="abonne-001", nouveau_index=150.0)
        result = CampagneMutations().saisir_index(info, input=input_data)
        self.assertEqual(result.statut, "RELEVE")
        mock_mut_client.saisir_index.assert_called_once_with(
            campagne_id="camp-001",
            abonne_id="abonne-001",
            nouveau_index=150.0,
            observation="",
            agent_id="agent-001",
            auteur_username="bob",
            auteur_role="AGENT",
        )

    @patch("schema.campagne_queries.campagne_client")
    @patch("schema.campagne_mutations.campagne_client")
    @patch("schema.campagne_mutations.require_auth")
    @patch("schema.campagne_mutations.require_role")
    def test_marquer_non_releve(self, mock_role, mock_auth, mock_mut_client, mock_query_client) -> None:
        from schema.campagne_types import MarquerNonReleveInput

        mock_auth.return_value = MagicMock(role="AGENT", user_id="agent-001")
        mock_mut_client.marquer_non_releve.return_value = _releve_response(statut="NON_RELEVE")
        mock_query_client.list_campagnes.return_value = MagicMock(campagnes=[MagicMock(campagne_id="camp-001")])
        info = MagicMock()
        input_data = MarquerNonReleveInput(campagne_id="camp-001", abonne_id="abonne-001", observation="Absent")
        result = CampagneMutations().marquer_non_releve(info, input=input_data)
        self.assertEqual(result.statut, "NON_RELEVE")

    @patch("schema.campagne_queries.campagne_client")
    @patch("schema.campagne_mutations.campagne_client")
    @patch("schema.campagne_mutations.require_auth")
    @patch("schema.campagne_mutations.require_role")
    def test_corriger_releve_admin(self, mock_role, mock_auth, mock_mut_client, mock_query_client) -> None:
        from schema.campagne_types import CorrigerReleveInput

        mock_auth.return_value = MagicMock(role="ADMIN", user_id="admin-001", username="alice")
        mock_mut_client.corriger_releve.return_value = _releve_response(nouveau_index=180.0, consommation=80.0)
        info = MagicMock()
        input_data = CorrigerReleveInput(
            campagne_id="camp-001", abonne_id="abonne-001", nouveau_index=180.0, observation="Erreur"
        )
        result = CampagneMutations().corriger_releve(info, input=input_data)
        self.assertEqual(result.nouveau_index, 180.0)
        mock_mut_client.corriger_releve.assert_called_once_with(
            campagne_id="camp-001",
            abonne_id="abonne-001",
            nouveau_index=180.0,
            observation="Erreur",
            auteur_id="admin-001",
            auteur_username="alice",
            auteur_role="ADMIN",
        )


class TestReleveMapping(SimpleTestCase):
    """Dérivation de saisiPar/saisiLe depuis le journal d'audit (P1)."""

    def test_saisi_par_derive_de_l_audit_saisie(self) -> None:
        from schema.campagne_types import releve_from_grpc

        r = _releve_response(agent_id="agent-001")
        r.audit.add(
            action="SAISIE",
            auteur_id="agent-001",
            auteur_username="bob",
            auteur_role="AGENT",
            ancien_index=100.0,
            nouvel_index=150.0,
            horodatage="2026-07-15T10:00:00+00:00",
        )
        r.audit.add(
            action="CORRECTION",
            auteur_id="admin-001",
            auteur_username="alice",
            auteur_role="ADMIN",
            ancien_index=100.0,
            nouvel_index=180.0,
            horodatage="2026-07-16T09:00:00+00:00",
        )
        releve = releve_from_grpc(r)
        self.assertEqual(releve.agent_id, "agent-001")
        self.assertIsNotNone(releve.saisi_par)
        self.assertEqual(releve.saisi_par.username, "bob")
        self.assertEqual(releve.saisi_par.role, "AGENT")
        self.assertEqual(releve.saisi_le, "2026-07-15T10:00:00+00:00")
        self.assertEqual([a.action for a in releve.audit], ["SAISIE", "CORRECTION"])

    def test_saisi_par_absent_si_pas_d_audit(self) -> None:
        from schema.campagne_types import releve_from_grpc

        releve = releve_from_grpc(_releve_response())
        self.assertIsNone(releve.saisi_par)
        self.assertEqual(releve.saisi_le, "")
        self.assertEqual(releve.audit, [])
