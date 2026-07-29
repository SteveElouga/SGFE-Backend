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
        created_by="user-001",
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
    def test_campagne_expose_created_by(self, mock_role, mock_auth, mock_client) -> None:
        """Le type GraphQL Campagne remonte createdBy (via campagne_from_grpc) —
        support du filtrage « mes campagnes » frontend selon le rôle SUPERVISEUR."""
        mock_auth.return_value = MagicMock(role="ADMIN", user_id="user-001")
        mock_client.get_campagne.return_value = _campagne_response(created_by="sup-042")
        result = CampagneQueries().campagne(MagicMock(), campagne_id="camp-001")
        self.assertEqual(result.created_by, "sup-042")

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

    @patch("schema.campagne_queries.abonne_client")
    @patch("schema.campagne_queries.campagne_client")
    @patch("schema.campagne_queries.require_auth")
    @patch("schema.campagne_queries.require_role")
    def test_releves_par_agent_utilise_la_tournee(self, mock_role, mock_auth, mock_client, mock_abonne) -> None:
        """Le resolver délègue le périmètre (zones/global) à ListRelevesTournee
        côté campagne-service et renvoie ses relevés tels quels (plus de filtrage
        client par agent_id, qui excluait les A_RELEVER)."""
        mock_auth.return_value = MagicMock(role="ADMIN", user_id="admin-001")
        mock_client.list_releves_tournee.return_value = MagicMock(
            releves=[
                _releve_response(releve_id="r1", abonne_id="ab-1", agent_id="agent-001", statut="RELEVE"),
                _releve_response(releve_id="r2", abonne_id="ab-2", agent_id="", statut="A_RELEVER"),
            ]
        )
        mock_abonne.list_abonnes.return_value = MagicMock(abonnes=[])
        info = MagicMock()
        result = CampagneQueries().releves_par_agent(info, campagne_id="camp-001", agent_id="agent-001")
        self.assertEqual([r.releve_id for r in result], ["r1", "r2"])
        mock_client.list_releves_tournee.assert_called_once_with("camp-001", "agent-001")

    @patch("schema.campagne_queries.abonne_client")
    @patch("schema.campagne_queries.campagne_client")
    @patch("schema.campagne_queries.require_auth")
    @patch("schema.campagne_queries.require_role")
    def test_releves_par_agent_enrichit_identite_abonne(self, mock_role, mock_auth, mock_client, mock_abonne) -> None:
        """La tournée est enrichie avec le nom/adresse/compteur de l'abonné (via
        Abonné Service, un seul ListAbonnes) : l'écran affiche des noms, pas des
        UUID. Un abonné inconnu laisse les champs vides sans faire échouer."""
        mock_auth.return_value = MagicMock(role="ADMIN", user_id="admin-001")
        mock_client.list_releves_tournee.return_value = MagicMock(
            releves=[
                _releve_response(releve_id="r1", abonne_id="ab-1", statut="A_RELEVER"),
                _releve_response(releve_id="r2", abonne_id="inconnu", statut="A_RELEVER"),
            ]
        )
        mock_abonne.list_abonnes.return_value = MagicMock(
            abonnes=[
                MagicMock(
                    abonne_id="ab-1",
                    nom="Ntsama",
                    prenom="Marie",
                    numero_abonne="AB-0001",
                    adresse="BEEDI",
                    compteur=MagicMock(numero_compteur=42),
                )
            ]
        )
        info = MagicMock()
        result = CampagneQueries().releves_par_agent(info, campagne_id="camp-001", agent_id="agent-001")
        mock_abonne.list_abonnes.assert_called_once_with()  # un seul appel (anti N+1)
        r1 = next(r for r in result if r.releve_id == "r1")
        self.assertEqual(r1.abonne_nom, "Ntsama")
        self.assertEqual(r1.numero_abonne, "AB-0001")
        self.assertEqual(r1.numero_compteur, 42)
        r2 = next(r for r in result if r.releve_id == "r2")
        self.assertEqual(r2.abonne_nom, "")  # abonné absent → champs vides, pas d'erreur

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
    def test_demarrer_campagne(self, mock_role, mock_auth, mock_client) -> None:
        mock_auth.return_value = MagicMock(role="ADMIN", user_id="user-001")
        mock_client.demarrer_campagne.return_value = _campagne_response(statut="EN_COURS")
        info = MagicMock()
        result = CampagneMutations().demarrer_campagne(info, campagne_id="camp-001")
        self.assertEqual(result.statut, "EN_COURS")

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

    @patch("schema.campagne_mutations.campagne_client")
    @patch("schema.campagne_mutations.require_auth")
    @patch("schema.campagne_mutations.require_role")
    def test_ajouter_abonnes_campagne(self, mock_role, mock_auth, mock_client) -> None:
        mock_auth.return_value = MagicMock(role="ADMIN", user_id="admin-001")
        mock_client.ajouter_abonnes_campagne.return_value = MagicMock(nb_ajoutes=2, nb_ignores=1)
        info = MagicMock()
        result = CampagneMutations().ajouter_abonnes_campagne(
            info, campagne_id="camp-001", abonne_ids=["ab-1", "ab-2", "ab-3"]
        )
        self.assertEqual(result.nb_ajoutes, 2)
        self.assertEqual(result.nb_ignores, 1)
        mock_client.ajouter_abonnes_campagne.assert_called_once_with("camp-001", ["ab-1", "ab-2", "ab-3"])

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


class TestDetailCampagneZones(SimpleTestCase):
    """Écran « détail campagne » : agents affectés, zones, statut de tournée."""

    def test_statut_tournee_seuils(self) -> None:
        from datetime import datetime, timedelta, timezone

        from schema.campagne_queries import _statut_tournee

        now = datetime.now(timezone.utc)
        self.assertEqual(_statut_tournee(""), "INACTIF")
        self.assertEqual(_statut_tournee((now - timedelta(minutes=5)).isoformat()), "EN_TOURNEE")
        self.assertEqual(_statut_tournee((now - timedelta(minutes=40)).isoformat()), "ACTIF")
        self.assertEqual(_statut_tournee((now - timedelta(hours=3)).isoformat()), "EN_RETARD")

    @patch("schema.campagne_queries.auth_client")
    @patch("schema.campagne_queries.abonne_client")
    @patch("schema.campagne_queries.campagne_client")
    @patch("schema.campagne_queries.require_auth")
    @patch("schema.campagne_queries.require_role")
    def test_agents_campagne_enrichi(self, mock_role, mock_auth, mock_camp, mock_ab, mock_authc) -> None:
        mock_auth.return_value = MagicMock(role="ADMIN", user_id="admin-1")
        zone = MagicMock(quartier="Plateau", camp=3, nb_releves=8)
        agent = MagicMock(agent_id="agent-1", zones=[zone], nb_releves=8, derniere_activite="")
        mock_camp.list_agents_campagne.return_value = MagicMock(agents=[agent])
        mock_ab.list_zones.return_value = MagicMock(zones=[MagicMock(quartier="Plateau", camp=3, nb_abonnes=10)])
        mock_authc.list_users.return_value = MagicMock(
            users=[MagicMock(user_id="agent-1", username="camara", role="AGENT")]
        )
        result = CampagneQueries().agents_campagne(MagicMock(), campagne_id="camp-1")
        self.assertEqual(len(result), 1)
        a = result[0]
        self.assertEqual(a.username, "camara")
        self.assertEqual(a.role, "AGENT")
        self.assertEqual(a.statut, "INACTIF")  # aucun relevé => pas d'activité
        self.assertEqual(a.zones[0].nb_abonnes, 10)
        self.assertEqual(a.zones[0].pct, 80.0)  # 8 / 10

    @patch("schema.campagne_queries.auth_client")
    @patch("schema.campagne_queries.abonne_client")
    @patch("schema.campagne_queries.campagne_client")
    @patch("schema.campagne_queries.require_auth")
    @patch("schema.campagne_queries.require_role")
    def test_repartition_par_zone(self, mock_role, mock_auth, mock_camp, mock_ab, mock_authc) -> None:
        mock_auth.return_value = MagicMock(role="ADMIN", user_id="admin-1")
        z1 = MagicMock(quartier="Plateau", camp=3, nb_releves=10)
        z2 = MagicMock(quartier="Centre", camp=1, nb_releves=6)
        agent = MagicMock(agent_id="agent-1", zones=[z1, z2], nb_releves=16, derniere_activite="")
        mock_camp.list_agents_campagne.return_value = MagicMock(agents=[agent])
        mock_ab.list_zones.return_value = MagicMock(
            zones=[
                MagicMock(quartier="Plateau", camp=3, nb_abonnes=10),
                MagicMock(quartier="Centre", camp=1, nb_abonnes=8),
            ]
        )
        mock_authc.list_users.return_value = MagicMock(
            users=[MagicMock(user_id="agent-1", username="camara", role="AGENT")]
        )
        result = CampagneQueries().repartition_par_zone(MagicMock(), campagne_id="camp-1")
        # trié par (quartier, camp) : Centre avant Plateau
        self.assertEqual([(r.quartier, r.camp) for r in result], [("Centre", 1), ("Plateau", 3)])
        self.assertEqual(result[0].agent_username, "camara")
        self.assertEqual(result[1].pct, 100.0)  # Plateau 10/10

    @patch("schema.campagne_queries.abonne_client")
    @patch("schema.campagne_queries.require_auth")
    @patch("schema.campagne_queries.require_role")
    def test_zones_disponibles(self, mock_role, mock_auth, mock_ab) -> None:
        mock_auth.return_value = MagicMock(role="ADMIN", user_id="admin-1")
        mock_ab.list_zones.return_value = MagicMock(zones=[MagicMock(quartier="Centre", camp=1, nb_abonnes=5)])
        result = CampagneQueries().zones_disponibles(MagicMock())
        self.assertEqual((result[0].quartier, result[0].camp, result[0].nb_abonnes), ("Centre", 1, 5))

    @patch("schema.campagne_mutations._enrichir_agents")
    @patch("schema.campagne_queries.campagne_client")
    @patch("schema.campagne_mutations.campagne_client")
    @patch("schema.campagne_mutations.require_auth")
    @patch("schema.campagne_mutations.require_role")
    def test_affecter_zones_mutation(
        self, mock_role, mock_auth, mock_mut_client, mock_query_client, mock_enrich
    ) -> None:
        from schema.campagne_types import ZoneInput

        mock_auth.return_value = MagicMock(role="ADMIN", user_id="admin-1")
        mock_mut_client.affecter_zones.return_value = MagicMock(agents=["raw"])
        mock_enrich.return_value = [MagicMock(agent_id="agent-1")]
        result = CampagneMutations().affecter_zones(
            MagicMock(),
            campagne_id="camp-1",
            agent_id="agent-1",
            zones=[ZoneInput(quartier="Plateau", camp=3), ZoneInput(quartier="Centre", camp=1)],
        )
        self.assertEqual(result[0].agent_id, "agent-1")
        _, kwargs = mock_mut_client.affecter_zones.call_args
        self.assertEqual(kwargs["campagne_id"], "camp-1")
        self.assertEqual(kwargs["agent_id"], "agent-1")
        self.assertEqual(len(kwargs["zones"]), 2)
