"""Tests unitaires du Campagne Service — logique métier."""

from django.core.exceptions import ObjectDoesNotExist, ValidationError
from django.test import TestCase

from campagnes.models import Campagne, StatutCampagne, StatutReleve
from campagnes.services import CampagneService, ReleveService


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
            self.svc.creer_campagne(
                nom="Test", periode_mois=1, periode_annee=2026, created_by=""
            )

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

    # --- ajouter abonné ---

    def test_ajouter_abonne_campagne_succes(self) -> None:
        c = self._creer_campagne(StatutCampagne.EN_COURS)
        releve = self.svc.ajouter_abonne_campagne(
            str(c.id), "abonne-001", ancien_index=100.0
        )
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

        releve = self.campagne_svc.ajouter_abonne_campagne(
            str(campagne.id), "abonne-001", ancien_index=100.0
        )
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
            self.svc.saisir_index(
                str(self.releve.id), nouveau_index=50.0, agent_id="agent-001"
            )

    def test_saisir_index_deja_releve_leve_erreur(self) -> None:
        self.svc.saisir_index(
            str(self.releve.id), nouveau_index=150.0, agent_id="agent-001"
        )
        with self.assertRaises(ValidationError):
            self.svc.saisir_index(
                str(self.releve.id), nouveau_index=200.0, agent_id="agent-001"
            )

    def test_saisir_index_campagne_non_en_cours_leve_erreur(self) -> None:
        from campagnes.repositories import CampagneRepository

        CampagneRepository().update_statut(self.campagne, StatutCampagne.CLOTUREE)
        with self.assertRaises(ValidationError):
            self.svc.saisir_index(
                str(self.releve.id), nouveau_index=150.0, agent_id="agent-001"
            )

    # --- marquer non relevé ---

    def test_marquer_non_releve_succes(self) -> None:
        releve = self.svc.marquer_non_releve(str(self.releve.id), observation="Absent")
        self.assertEqual(releve.statut, StatutReleve.NON_RELEVE)
        self.assertEqual(releve.observation, "Absent")

    def test_marquer_non_releve_campagne_cloturee_leve_erreur(self) -> None:
        from campagnes.repositories import CampagneRepository

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
        self.campagne_svc.ajouter_abonne_campagne(
            str(self.campagne.id), "abonne-002", 200.0
        )
        releves = self.svc.list_releves(str(self.campagne.id))
        self.assertEqual(len(releves), 2)
