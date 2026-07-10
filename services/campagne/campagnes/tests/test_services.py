"""Tests unitaires du Campagne Service — logique métier."""

from types import SimpleNamespace
from unittest.mock import patch

from django.core.exceptions import ObjectDoesNotExist, ValidationError
from django.test import TestCase

from campagnes.grpc_clients import AbonneServiceClient
from campagnes.models import Campagne, StatutCampagne, StatutReleve
from campagnes.repositories import CampagneAgentRepository, CampagneRepository
from campagnes.services import CampagneService, ReleveService

# ajouter_abonne_campagne vérifie désormais le statut ACTIF de l'abonné
# (ANO-003) via un appel gRPC réel à Abonné Service. On patche cet appel
# pour tout le module afin que les tests existants (non liés à cette
# vérification) n'aient pas besoin d'un Abonné Service en cours d'exécution.
_abonne_patcher = patch.object(AbonneServiceClient, "get_abonne", return_value=SimpleNamespace(statut="ACTIF"))


def setUpModule() -> None:
    _abonne_patcher.start()


def tearDownModule() -> None:
    _abonne_patcher.stop()


class TestCampagneService(TestCase):
    """Tests de CampagneService."""

    def setUp(self) -> None:
        self.svc = CampagneService()
        self.created_by = "user-uuid-001"

    def _creer_campagne(self, statut: str = StatutCampagne.PLANIFIEE) -> Campagne:
        c = self.svc.creer_campagne(
            nom="Campagne Juin 2026",
            periode_mois=6,
            periode_annee=2026,
            created_by=self.created_by,
        )
        if statut != StatutCampagne.PLANIFIEE:
            from campagnes.repositories import CampagneRepository

            CampagneRepository().update_statut(c, statut)
            c.refresh_from_db()
        return c

    # --- création ---

    def test_creer_campagne_succes(self) -> None:
        c = self.svc.creer_campagne(
            nom="Campagne Test",
            periode_mois=3,
            periode_annee=2026,
            created_by=self.created_by,
        )
        self.assertEqual(c.statut, StatutCampagne.PLANIFIEE)
        self.assertEqual(c.periode_mois, 3)
        self.assertEqual(c.created_by, self.created_by)

    def test_creer_campagne_demarrer_maintenant_statut_en_cours(self) -> None:
        c = self.svc.creer_campagne(
            nom="Campagne Immédiate",
            periode_mois=7,
            periode_annee=2026,
            created_by=self.created_by,
            demarrer_maintenant=True,
        )
        self.assertEqual(c.statut, StatutCampagne.EN_COURS)

    def test_creer_campagne_sans_demarrer_maintenant_reste_planifiee(self) -> None:
        c = self.svc.creer_campagne(
            nom="Campagne Planifiée",
            periode_mois=7,
            periode_annee=2026,
            created_by=self.created_by,
            demarrer_maintenant=False,
        )
        self.assertEqual(c.statut, StatutCampagne.PLANIFIEE)

    def test_creer_campagne_nom_vide_leve_erreur(self) -> None:
        with self.assertRaises(ValidationError):
            self.svc.creer_campagne(
                nom="   ",
                periode_mois=1,
                periode_annee=2026,
                created_by=self.created_by,
            )

    def test_creer_campagne_mois_invalide_leve_erreur(self) -> None:
        with self.assertRaises(ValidationError):
            self.svc.creer_campagne(
                nom="Test",
                periode_mois=13,
                periode_annee=2026,
                created_by=self.created_by,
            )

    def test_creer_campagne_without_created_by_leve_erreur(self) -> None:
        with self.assertRaises(ValidationError):
            self.svc.creer_campagne(nom="Test", periode_mois=1, periode_annee=2026, created_by="")

    # --- démarrer ---

    def test_demarrer_campagne_planifiee_succes(self) -> None:
        c = self._creer_campagne()
        updated = self.svc.demarrer_campagne(str(c.id))
        self.assertEqual(updated.statut, StatutCampagne.EN_COURS)

    def test_demarrer_campagne_non_planifiee_leve_erreur(self) -> None:
        c = self._creer_campagne(StatutCampagne.EN_COURS)
        with self.assertRaises(ValidationError):
            self.svc.demarrer_campagne(str(c.id))

    def test_demarrer_campagne_inexistante_leve_erreur(self) -> None:
        with self.assertRaises(ObjectDoesNotExist):
            self.svc.demarrer_campagne("00000000-0000-0000-0000-000000000000")

    # --- clôturer ---

    def test_cloturer_campagne_en_cours_succes(self) -> None:
        c = self._creer_campagne(StatutCampagne.EN_COURS)
        cloturee = self.svc.cloturer_campagne(str(c.id))
        self.assertEqual(cloturee.statut, StatutCampagne.CLOTUREE)
        self.assertIsNotNone(cloturee.date_cloture)

    def test_cloturer_campagne_planifiee_leve_erreur(self) -> None:
        c = self._creer_campagne()
        with self.assertRaises(ValidationError):
            self.svc.cloturer_campagne(str(c.id))

    # --- filtrage SUPERVISEUR ---

    def test_list_campagnes_filtre_created_by(self) -> None:
        self.svc.creer_campagne("C1", 1, 2026, created_by="user-A")
        self.svc.creer_campagne("C2", 2, 2026, created_by="user-B")
        self.svc.creer_campagne("C3", 3, 2026, created_by="user-A")

        resultat = self.svc.list_campagnes(created_by="user-A")
        self.assertEqual(len(resultat), 2)
        self.assertTrue(all(c.created_by == "user-A" for c in resultat))

    def test_list_campagnes_sans_filtre_retourne_tout(self) -> None:
        self.svc.creer_campagne("C1", 1, 2026, created_by="user-A")
        self.svc.creer_campagne("C2", 2, 2026, created_by="user-B")

        resultat = self.svc.list_campagnes()
        self.assertEqual(len(resultat), 2)

    # --- progression ---

    def test_get_progression_vide(self) -> None:
        c = self._creer_campagne(StatutCampagne.EN_COURS)
        counts = self.svc.get_progression(str(c.id))
        total = sum(counts.values())
        self.assertEqual(total, 0)

    def test_get_resume_cloture(self) -> None:
        c = self._creer_campagne(StatutCampagne.EN_COURS)
        # 2 relevés, 1 estimé, 1 non relevé, 1 restant (A_RELEVER)
        statuts = [
            StatutReleve.RELEVE,
            StatutReleve.RELEVE,
            StatutReleve.ESTIME,
            StatutReleve.NON_RELEVE,
            StatutReleve.A_RELEVER,
        ]
        for i, s in enumerate(statuts):
            r = self.svc.ajouter_abonne_campagne(str(c.id), f"abonne-{i:03d}", ancien_index=0.0)
            r.statut = s
            r.save()

        resume = self.svc.get_resume_cloture(str(c.id))

        self.assertEqual(resume["total_abonnes"], 5)
        self.assertEqual(resume["nb_releves"], 2)
        self.assertEqual(resume["nb_estimes"], 1)
        self.assertEqual(resume["nb_non_releves"], 1)
        self.assertEqual(resume["nb_restants"], 1)
        # Seuls relevés + estimés sont facturés
        self.assertEqual(resume["nb_factures_a_generer"], 3)

    def test_get_resume_cloture_campagne_introuvable_leve_erreur(self) -> None:
        import uuid as _uuid

        with self.assertRaises(ObjectDoesNotExist):
            self.svc.get_resume_cloture(str(_uuid.uuid4()))

    # --- ajouter abonné ---

    def test_ajouter_abonne_campagne_succes(self) -> None:
        c = self._creer_campagne(StatutCampagne.EN_COURS)
        releve = self.svc.ajouter_abonne_campagne(str(c.id), "abonne-001", ancien_index=100.0)
        self.assertEqual(releve.statut, StatutReleve.A_RELEVER)
        self.assertEqual(releve.ancien_index, 100.0)

    def test_ajouter_abonne_campagne_cloturee_leve_erreur(self) -> None:
        c = self._creer_campagne(StatutCampagne.CLOTUREE)
        with self.assertRaises(ValidationError):
            self.svc.ajouter_abonne_campagne(str(c.id), "abonne-001", ancien_index=0.0)

    def test_ajouter_abonne_en_double_leve_erreur(self) -> None:
        c = self._creer_campagne(StatutCampagne.EN_COURS)
        self.svc.ajouter_abonne_campagne(str(c.id), "abonne-001", ancien_index=0.0)
        with self.assertRaises(ValidationError):
            self.svc.ajouter_abonne_campagne(str(c.id), "abonne-001", ancien_index=0.0)

    def test_ajouter_abonne_suspendu_leve_erreur(self) -> None:
        """Régression ANO-003 : un abonné non ACTIF ne peut pas être ajouté à une campagne."""
        c = self._creer_campagne(StatutCampagne.EN_COURS)
        with patch.object(AbonneServiceClient, "get_abonne", return_value=SimpleNamespace(statut="SUSPENDU")):
            with self.assertRaises(ValidationError):
                self.svc.ajouter_abonne_campagne(str(c.id), "abonne-suspendu", ancien_index=0.0)

    def test_ajouter_abonne_service_indisponible_leve_erreur(self) -> None:
        """Régression ANO-003 : la vérification est bloquante, pas dégradée — si
        Abonné Service est injoignable, l'ajout doit échouer plutôt que de
        passer outre la règle métier obligatoire."""
        import grpc

        c = self._creer_campagne(StatutCampagne.EN_COURS)
        with patch.object(AbonneServiceClient, "get_abonne", side_effect=grpc.RpcError("indisponible")):
            with self.assertRaises(ValidationError):
                self.svc.ajouter_abonne_campagne(str(c.id), "abonne-001", ancien_index=0.0)


class TestReleveService(TestCase):
    """Tests de ReleveService."""

    def setUp(self) -> None:
        self.svc = ReleveService()
        self.campagne_svc = CampagneService()
        campagne = self.campagne_svc.creer_campagne(
            nom="Campagne Test", periode_mois=6, periode_annee=2026, created_by="user-A"
        )
        from campagnes.repositories import CampagneRepository

        CampagneRepository().update_statut(campagne, StatutCampagne.EN_COURS)
        self.campagne = campagne

        releve = self.campagne_svc.ajouter_abonne_campagne(str(campagne.id), "abonne-001", ancien_index=100.0)
        self.releve = releve

    # --- saisir index ---

    def test_saisir_index_succes(self) -> None:
        releve = self.svc.saisir_index(
            str(self.releve.id),
            nouveau_index=150.0,
            agent_id="agent-001",
        )
        self.assertEqual(releve.statut, StatutReleve.RELEVE)
        self.assertEqual(releve.consommation, 50.0)
        self.assertEqual(releve.agent_id, "agent-001")

    def test_saisir_index_inferieur_a_ancien_leve_erreur(self) -> None:
        with self.assertRaises(ValidationError):
            self.svc.saisir_index(str(self.releve.id), nouveau_index=50.0, agent_id="agent-001")

    def test_saisir_index_deja_releve_leve_erreur(self) -> None:
        self.svc.saisir_index(str(self.releve.id), nouveau_index=150.0, agent_id="agent-001")
        with self.assertRaises(ValidationError):
            self.svc.saisir_index(str(self.releve.id), nouveau_index=200.0, agent_id="agent-001")

    def test_saisir_index_campagne_non_en_cours_leve_erreur(self) -> None:
        from campagnes.repositories import CampagneRepository

        CampagneRepository().update_statut(self.campagne, StatutCampagne.CLOTUREE)
        with self.assertRaises(ValidationError):
            self.svc.saisir_index(str(self.releve.id), nouveau_index=150.0, agent_id="agent-001")

    def test_saisir_index_cree_audit_saisie(self) -> None:
        releve = self.svc.saisir_index(
            str(self.releve.id),
            nouveau_index=150.0,
            agent_id="agent-001",
            auteur_username="bob",
            auteur_role="AGENT",
        )
        audits = list(releve.audits.all())
        self.assertEqual(len(audits), 1)
        self.assertEqual(audits[0].action, "SAISIE")
        self.assertEqual(audits[0].auteur_id, "agent-001")
        self.assertEqual(audits[0].auteur_username, "bob")
        self.assertEqual(audits[0].auteur_role, "AGENT")
        self.assertEqual(audits[0].ancien_index, 100.0)
        self.assertEqual(audits[0].nouvel_index, 150.0)

    # --- corriger relevé ---

    def test_corriger_releve_succes_et_audit(self) -> None:
        self.svc.saisir_index(str(self.releve.id), nouveau_index=150.0, agent_id="agent-001")
        releve = self.svc.corriger_releve(
            str(self.releve.id),
            nouveau_index=180.0,
            auteur_id="admin-001",
            auteur_username="alice",
            auteur_role="ADMIN",
            observation="Erreur de lecture",
        )
        self.assertEqual(releve.nouveau_index, 180.0)
        self.assertEqual(releve.consommation, 80.0)
        self.assertEqual(releve.statut, StatutReleve.RELEVE)
        # agent_id d'origine préservé (l'auteur de la correction n'écrase pas la saisie)
        self.assertEqual(releve.agent_id, "agent-001")
        actions = [a.action for a in releve.audits.all()]
        self.assertEqual(actions, ["SAISIE", "CORRECTION"])
        correction = releve.audits.all()[1]
        self.assertEqual(correction.auteur_id, "admin-001")
        self.assertEqual(correction.nouvel_index, 180.0)

    def test_corriger_releve_autorise_apres_cloture(self) -> None:
        self.svc.saisir_index(str(self.releve.id), nouveau_index=150.0, agent_id="agent-001")
        CampagneRepository().update_statut(self.campagne, StatutCampagne.CLOTUREE)
        releve = self.svc.corriger_releve(str(self.releve.id), nouveau_index=175.0, auteur_id="admin-001")
        self.assertEqual(releve.nouveau_index, 175.0)

    def test_corriger_releve_non_saisi_leve_erreur(self) -> None:
        # Le relevé est encore A_RELEVER : rien à corriger.
        with self.assertRaises(ValidationError):
            self.svc.corriger_releve(str(self.releve.id), nouveau_index=150.0, auteur_id="admin-001")

    def test_corriger_releve_inferieur_a_ancien_leve_erreur(self) -> None:
        self.svc.saisir_index(str(self.releve.id), nouveau_index=150.0, agent_id="agent-001")
        with self.assertRaises(ValidationError):
            self.svc.corriger_releve(str(self.releve.id), nouveau_index=50.0, auteur_id="admin-001")

    def test_corriger_releve_audit_conserve_la_valeur_remplacee(self) -> None:
        """L'audit CORRECTION trace la valeur relevée AVANT correction (l'index
        remplacé), et non l'index compteur de base — sinon le journal est
        trompeur et la valeur remplacée est perdue."""
        # ancien_index (compteur) = 100 ; saisie initiale = 150 ; correction = 180.
        self.svc.saisir_index(str(self.releve.id), nouveau_index=150.0, agent_id="agent-001")
        releve = self.svc.corriger_releve(str(self.releve.id), nouveau_index=180.0, auteur_id="admin-001")
        correction = releve.audits.all()[1]
        self.assertEqual(correction.action, "CORRECTION")
        self.assertEqual(correction.ancien_index, 150.0)  # la valeur remplacée, pas 100
        self.assertEqual(correction.nouvel_index, 180.0)

    # --- marquer non relevé / estimé ---

    def test_marquer_non_releve_succes(self) -> None:
        releve = self.svc.marquer_non_releve(str(self.releve.id), statut=StatutReleve.NON_RELEVE, observation="Absent")
        self.assertEqual(releve.statut, StatutReleve.NON_RELEVE)
        self.assertEqual(releve.observation, "Absent")

    def test_marquer_estime_succes(self) -> None:
        releve = self.svc.marquer_non_releve(
            str(self.releve.id),
            statut=StatutReleve.ESTIME,
            observation="Compteur illisible",
        )
        self.assertEqual(releve.statut, StatutReleve.ESTIME)

    def test_marquer_statut_invalide_leve_erreur(self) -> None:
        with self.assertRaises(ValidationError):
            self.svc.marquer_non_releve(str(self.releve.id), statut="RELEVE")

    def test_marquer_releve_deja_saisi_leve_erreur(self) -> None:
        self.svc.saisir_index(str(self.releve.id), nouveau_index=150.0, agent_id="agent-001")
        with self.assertRaises(ValidationError):
            self.svc.marquer_non_releve(str(self.releve.id), statut=StatutReleve.NON_RELEVE)

    def test_marquer_non_releve_campagne_cloturee_leve_erreur(self) -> None:
        CampagneRepository().update_statut(self.campagne, StatutCampagne.CLOTUREE)
        with self.assertRaises(ValidationError):
            self.svc.marquer_non_releve(str(self.releve.id))

    # --- get / list ---

    def test_get_releve_existant(self) -> None:
        releve = self.svc.get_releve(str(self.releve.id))
        self.assertEqual(releve.abonne_id, "abonne-001")

    def test_get_releve_inexistant_leve_erreur(self) -> None:
        with self.assertRaises(ObjectDoesNotExist):
            self.svc.get_releve("00000000-0000-0000-0000-000000000000")

    def test_list_releves_par_campagne(self) -> None:
        self.campagne_svc.ajouter_abonne_campagne(str(self.campagne.id), "abonne-002", 200.0)
        releves = self.svc.list_releves(str(self.campagne.id))
        self.assertEqual(len(releves), 2)


class TestCampagneAgentRepository(TestCase):
    """Tests de CampagneAgentRepository."""

    def setUp(self) -> None:
        self.repo = CampagneAgentRepository()
        self.campagne = CampagneService().creer_campagne(
            nom="Campagne Test", periode_mois=6, periode_annee=2026, created_by="user-A"
        )

    def test_assigner_agent_cree_affectation(self) -> None:
        affectation = self.repo.assigner(self.campagne, agent_id="agent-001")
        self.assertEqual(affectation.agent_id, "agent-001")
        self.assertEqual(affectation.campagne_id, self.campagne.id)

    def test_assigner_agent_idempotent(self) -> None:
        self.repo.assigner(self.campagne, agent_id="agent-001")
        self.repo.assigner(self.campagne, agent_id="agent-001")  # pas d'exception
        from campagnes.models import CampagneAgent

        count = CampagneAgent.objects.filter(campagne=self.campagne, agent_id="agent-001").count()
        self.assertEqual(count, 1)

    def test_est_affecte_retourne_vrai(self) -> None:
        self.repo.assigner(self.campagne, agent_id="agent-001")
        self.assertTrue(self.repo.est_affecte(str(self.campagne.id), "agent-001"))

    def test_est_affecte_retourne_faux_si_non_affecte(self) -> None:
        self.assertFalse(self.repo.est_affecte(str(self.campagne.id), "agent-inconnu"))

    def test_filtre_list_campagnes_par_agent(self) -> None:
        c2 = CampagneService().creer_campagne(
            nom="Autre campagne",
            periode_mois=7,
            periode_annee=2026,
            created_by="user-B",
        )
        self.repo.assigner(self.campagne, agent_id="agent-001")
        self.repo.assigner(c2, agent_id="agent-002")
        campagnes = CampagneRepository().list_all(agent_id="agent-001")
        self.assertEqual(len(campagnes), 1)
        self.assertEqual(campagnes[0].id, self.campagne.id)


class TestScheduler(TestCase):
    """Tests du cron 7h00 — démarrage des campagnes planifiées."""

    def setUp(self) -> None:
        self.svc = CampagneService()

    def test_demarrage_campagne_planifiee_pour_aujourd_hui(self) -> None:
        from datetime import date
        from campagnes.schedulers import campagne_planifiee_job

        campagne = self.svc.creer_campagne(
            nom="Campagne Aujourd'hui",
            periode_mois=6,
            periode_annee=2026,
            created_by="user-A",
            date_planifiee=str(date.today()),
        )
        campagne_planifiee_job()
        campagne.refresh_from_db()
        self.assertEqual(campagne.statut, StatutCampagne.EN_COURS)

    def test_demarrage_plusieurs_campagnes_meme_date_planifiee(self) -> None:
        """Régression ANO-019 : si plusieurs campagnes partagent la même
        date_planifiee, elles doivent TOUTES démarrer (auparavant .first()
        n'en démarrait qu'une seule, les autres restaient bloquées PLANIFIEE
        indéfiniment)."""
        from datetime import date

        aujourdhui = str(date.today())
        c1 = self.svc.creer_campagne(
            nom="Campagne A", periode_mois=6, periode_annee=2026, created_by="user-A", date_planifiee=aujourdhui
        )
        c2 = self.svc.creer_campagne(
            nom="Campagne B", periode_mois=6, periode_annee=2026, created_by="user-B", date_planifiee=aujourdhui
        )

        demarrees = self.svc.demarrer_campagnes_planifiees_pour_aujourd_hui()

        self.assertEqual({c.id for c in demarrees}, {c1.id, c2.id})
        c1.refresh_from_db()
        c2.refresh_from_db()
        self.assertEqual(c1.statut, StatutCampagne.EN_COURS)
        self.assertEqual(c2.statut, StatutCampagne.EN_COURS)

    def test_aucune_campagne_planifiee_ne_change_rien(self) -> None:
        from datetime import date, timedelta
        from campagnes.schedulers import campagne_planifiee_job

        campagne = self.svc.creer_campagne(
            nom="Campagne Demain",
            periode_mois=7,
            periode_annee=2026,
            created_by="user-A",
            date_planifiee=str(date.today() + timedelta(days=1)),
        )
        campagne_planifiee_job()
        campagne.refresh_from_db()
        self.assertEqual(campagne.statut, StatutCampagne.PLANIFIEE)


class TestAffectationZone(TestCase):
    """Affectation des agents par zone + agrégation ListAgentsCampagne."""

    def setUp(self) -> None:
        self.svc = CampagneService()
        self.releve_svc = ReleveService()
        campagne = self.svc.creer_campagne("C", 6, 2026, created_by="user-A")
        CampagneRepository().update_statut(campagne, StatutCampagne.EN_COURS)
        self.campagne = campagne

    def _ajouter(self, abonne_id: str, quartier: str, camp: int, ancien_index: float = 0.0):
        with patch.object(
            AbonneServiceClient,
            "get_abonne",
            return_value=SimpleNamespace(statut="ACTIF", compteur=SimpleNamespace(quartier=quartier, camp=camp)),
        ):
            return self.svc.ajouter_abonne_campagne(str(self.campagne.id), abonne_id, ancien_index)

    def test_snapshot_zone_sur_releve(self) -> None:
        releve = self._ajouter("ab-1", "Plateau", 3)
        self.assertEqual(releve.quartier, "Plateau")
        self.assertEqual(releve.camp, 3)

    def test_agent_ne_peut_pas_saisir_hors_de_ses_zones(self) -> None:
        """Cloisonnement en écriture : un AGENT ne peut saisir un relevé hors de
        ses zones affectées (symétrique du filtrage de tournée)."""
        r_hors = self._ajouter("ab-hors", "Centre", 1, ancien_index=10.0)
        self.svc.affecter_zones(str(self.campagne.id), "agent-1", [("Plateau", 3)])
        with self.assertRaises(ValidationError):
            self.releve_svc.saisir_index(str(r_hors.id), nouveau_index=20.0, agent_id="agent-1", auteur_role="AGENT")

    def test_agent_peut_saisir_dans_sa_zone(self) -> None:
        r_dans = self._ajouter("ab-dans", "Plateau", 3, ancien_index=10.0)
        self.svc.affecter_zones(str(self.campagne.id), "agent-1", [("Plateau", 3)])
        releve = self.releve_svc.saisir_index(
            str(r_dans.id), nouveau_index=20.0, agent_id="agent-1", auteur_role="AGENT"
        )
        self.assertEqual(releve.statut, StatutReleve.RELEVE)

    def test_agent_sans_zone_couvre_toute_la_campagne(self) -> None:
        """Un agent affecté globalement mais sans zone n'est pas restreint."""
        r = self._ajouter("ab-x", "Centre", 1, ancien_index=10.0)
        releve = self.releve_svc.saisir_index(str(r.id), nouveau_index=20.0, agent_id="agent-1", auteur_role="AGENT")
        self.assertEqual(releve.statut, StatutReleve.RELEVE)

    def test_admin_non_restreint_par_les_zones(self) -> None:
        """ADMIN/SUPERVISEUR ne sont jamais restreints au périmètre de zones."""
        r_hors = self._ajouter("ab-hors2", "Centre", 1, ancien_index=10.0)
        self.svc.affecter_zones(str(self.campagne.id), "agent-1", [("Plateau", 3)])
        releve = self.releve_svc.saisir_index(
            str(r_hors.id), nouveau_index=20.0, agent_id="agent-1", auteur_role="ADMIN"
        )
        self.assertEqual(releve.statut, StatutReleve.RELEVE)

    def test_affecter_zones_cree_affectation_globale(self) -> None:
        agents = self.svc.affecter_zones(str(self.campagne.id), "agent-1", [("Plateau", 3), ("Centre", 1)])
        self.assertTrue(CampagneAgentRepository().est_affecte(str(self.campagne.id), "agent-1"))
        agent = next(a for a in agents if a["agent_id"] == "agent-1")
        zones = {(z["quartier"], z["camp"]) for z in agent["zones"]}
        self.assertEqual(zones, {("Plateau", 3), ("Centre", 1)})

    def test_affecter_zones_reaffecte_une_zone(self) -> None:
        self.svc.affecter_zones(str(self.campagne.id), "agent-1", [("Plateau", 3), ("Centre", 1)])
        # Plateau·3 passe à agent-2 ; agent-1 ne garde que Centre·1.
        self.svc.affecter_zones(str(self.campagne.id), "agent-2", [("Plateau", 3)])
        agents = {a["agent_id"]: a for a in self.svc.list_agents_campagne(str(self.campagne.id))}
        self.assertEqual({(z["quartier"], z["camp"]) for z in agents["agent-1"]["zones"]}, {("Centre", 1)})
        self.assertEqual({(z["quartier"], z["camp"]) for z in agents["agent-2"]["zones"]}, {("Plateau", 3)})

    def test_list_agents_campagne_stats(self) -> None:
        # 2 abonnés Plateau·3, 1 relevé saisi par agent-1.
        r1 = self._ajouter("ab-1", "Plateau", 3, ancien_index=100.0)
        self._ajouter("ab-2", "Plateau", 3, ancien_index=50.0)
        self.releve_svc.saisir_index(str(r1.id), nouveau_index=150.0, agent_id="agent-1")
        self.svc.affecter_zones(str(self.campagne.id), "agent-1", [("Plateau", 3)])

        agent = next(a for a in self.svc.list_agents_campagne(str(self.campagne.id)) if a["agent_id"] == "agent-1")
        self.assertEqual(agent["nb_releves"], 1)
        self.assertIsNotNone(agent["derniere_activite"])
        zone = agent["zones"][0]
        self.assertEqual((zone["quartier"], zone["camp"]), ("Plateau", 3))
        self.assertEqual(zone["nb_releves"], 1)  # 1 relevé RELEVE dans la zone

    def test_agent_global_sans_zone_apparait(self) -> None:
        CampagneAgentRepository().assigner(self.campagne, "agent-global")
        agents = {a["agent_id"]: a for a in self.svc.list_agents_campagne(str(self.campagne.id))}
        self.assertIn("agent-global", agents)
        self.assertEqual(agents["agent-global"]["zones"], [])
