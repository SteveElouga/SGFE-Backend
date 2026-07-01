"""Tests unitaires des services du Paiement Service."""

from datetime import date, timedelta
from decimal import Decimal
from unittest.mock import MagicMock, patch

from django.core.exceptions import ObjectDoesNotExist, ValidationError
from django.test import TestCase

from paiements.models import ModePaiement, SoldeFacture, StatutSolde, SuiviImpaye
from paiements.repositories import SoldeFactureRepository
from paiements.services import ImpayeService, PaiementService


def _creer_solde(
    facture_id: str = "facture-001",
    abonne_id: str = "abonne-001",
    montant_total: float = 300.00,
    date_limite: date | None = None,
) -> SoldeFacture:
    """Crée un SoldeFacture de test via le repository."""
    repo = SoldeFactureRepository()
    return repo.create(
        facture_id=facture_id,
        abonne_id=abonne_id,
        montant_total=Decimal(str(montant_total)),
        date_limite_paiement=date_limite or date(2026, 7, 1),
    )


class TestInitialiserSolde(TestCase):
    """Tests de PaiementService.initialiser_solde."""

    def setUp(self) -> None:
        self.svc = PaiementService()

    def test_initialiser_solde_succes(self) -> None:
        """Crée un SoldeFacture avec statut IMPAYEE et montant_paye=0."""
        solde = self.svc.initialiser_solde(
            facture_id="facture-001",
            abonne_id="abonne-001",
            montant_total=300.00,
            date_limite_paiement=date(2026, 7, 1),
        )
        self.assertEqual(solde.facture_id, "facture-001")
        self.assertEqual(solde.statut, StatutSolde.IMPAYEE)
        self.assertEqual(solde.montant_paye, Decimal("0"))
        self.assertEqual(solde.solde_restant, Decimal("300.00"))
        self.assertEqual(solde.montant_total, Decimal("300.00"))

    def test_initialiser_solde_montant_nul_leve_erreur(self) -> None:
        """Un montant total nul ou négatif lève une ValidationError."""
        with self.assertRaises(ValidationError):
            self.svc.initialiser_solde(
                facture_id="facture-002",
                abonne_id="abonne-001",
                montant_total=0.0,
                date_limite_paiement=date(2026, 7, 1),
            )

    def test_initialiser_solde_facture_id_vide_leve_erreur(self) -> None:
        """Un facture_id vide lève une ValidationError."""
        with self.assertRaises(ValidationError):
            self.svc.initialiser_solde(
                facture_id="",
                abonne_id="abonne-001",
                montant_total=100.0,
                date_limite_paiement=date(2026, 7, 1),
            )

    def test_initialiser_solde_abonne_id_vide_leve_erreur(self) -> None:
        """Un abonne_id vide lève une ValidationError."""
        with self.assertRaises(ValidationError):
            self.svc.initialiser_solde(
                facture_id="facture-003",
                abonne_id="",
                montant_total=100.0,
                date_limite_paiement=date(2026, 7, 1),
            )


class TestEnregistrerPaiement(TestCase):
    """Tests de PaiementService.enregistrer_paiement."""

    def setUp(self) -> None:
        self.svc = PaiementService()
        _creer_solde("facture-001", "abonne-001", 300.00)

    def test_paiement_partiel_statut_partielle(self) -> None:
        """Un versement partiel passe le solde en PARTIELLE."""
        paiement, solde = self.svc.enregistrer_paiement(
            facture_id="facture-001",
            abonne_id="abonne-001",
            montant=100.00,
            date_paiement=date(2026, 6, 20),
            mode_paiement=ModePaiement.ESPECES,
            reference_transaction="",
            enregistre_par="user-001",
        )
        self.assertIsNotNone(paiement.id)
        self.assertEqual(solde.statut, StatutSolde.PARTIELLE)
        self.assertEqual(solde.montant_paye, Decimal("100.00"))
        self.assertEqual(solde.solde_restant, Decimal("200.00"))

    def test_paiement_total_statut_payee(self) -> None:
        """Un versement total passe le solde en PAYEE."""
        paiement, solde = self.svc.enregistrer_paiement(
            facture_id="facture-001",
            abonne_id="abonne-001",
            montant=300.00,
            date_paiement=date(2026, 6, 20),
            mode_paiement=ModePaiement.ESPECES,
            reference_transaction="",
            enregistre_par="user-001",
        )
        self.assertEqual(solde.statut, StatutSolde.PAYEE)
        self.assertEqual(solde.solde_restant, Decimal("0.00"))

    def test_deux_versements_successifs_statut_payee(self) -> None:
        """Deux versements successifs mènent au statut PAYEE."""
        self.svc.enregistrer_paiement(
            facture_id="facture-001",
            abonne_id="abonne-001",
            montant=150.00,
            date_paiement=date(2026, 6, 20),
            mode_paiement=ModePaiement.ESPECES,
            reference_transaction="",
            enregistre_par="user-001",
        )
        _, solde = self.svc.enregistrer_paiement(
            facture_id="facture-001",
            abonne_id="abonne-001",
            montant=150.00,
            date_paiement=date(2026, 6, 22),
            mode_paiement=ModePaiement.ESPECES,
            reference_transaction="",
            enregistre_par="user-001",
        )
        self.assertEqual(solde.statut, StatutSolde.PAYEE)

    def test_montant_nul_leve_erreur(self) -> None:
        """Un montant de paiement nul lève une ValidationError."""
        with self.assertRaises(ValidationError):
            self.svc.enregistrer_paiement(
                facture_id="facture-001",
                abonne_id="abonne-001",
                montant=0.0,
                date_paiement=date(2026, 6, 20),
                mode_paiement=ModePaiement.ESPECES,
                reference_transaction="",
                enregistre_par="user-001",
            )

    def test_montant_negatif_leve_erreur(self) -> None:
        """Un montant négatif lève une ValidationError."""
        with self.assertRaises(ValidationError):
            self.svc.enregistrer_paiement(
                facture_id="facture-001",
                abonne_id="abonne-001",
                montant=-50.0,
                date_paiement=date(2026, 6, 20),
                mode_paiement=ModePaiement.ESPECES,
                reference_transaction="",
                enregistre_par="user-001",
            )

    def test_surpaiement_leve_erreur(self) -> None:
        """Un montant supérieur au solde restant lève une ValidationError."""
        with self.assertRaises(ValidationError):
            self.svc.enregistrer_paiement(
                facture_id="facture-001",
                abonne_id="abonne-001",
                montant=400.00,
                date_paiement=date(2026, 6, 20),
                mode_paiement=ModePaiement.ESPECES,
                reference_transaction="",
                enregistre_par="user-001",
            )

    def test_reference_obligatoire_mobile_money(self) -> None:
        """La référence de transaction est obligatoire pour MOBILE_MONEY."""
        with self.assertRaises(ValidationError):
            self.svc.enregistrer_paiement(
                facture_id="facture-001",
                abonne_id="abonne-001",
                montant=100.00,
                date_paiement=date(2026, 6, 20),
                mode_paiement=ModePaiement.MOBILE_MONEY,
                reference_transaction="",
                enregistre_par="user-001",
            )

    def test_reference_obligatoire_virement(self) -> None:
        """La référence de transaction est obligatoire pour VIREMENT."""
        with self.assertRaises(ValidationError):
            self.svc.enregistrer_paiement(
                facture_id="facture-001",
                abonne_id="abonne-001",
                montant=100.00,
                date_paiement=date(2026, 6, 20),
                mode_paiement=ModePaiement.VIREMENT,
                reference_transaction="",
                enregistre_par="user-001",
            )

    def test_reference_fournie_mobile_money_succes(self) -> None:
        """MOBILE_MONEY avec référence fournie est accepté."""
        paiement, solde = self.svc.enregistrer_paiement(
            facture_id="facture-001",
            abonne_id="abonne-001",
            montant=100.00,
            date_paiement=date(2026, 6, 20),
            mode_paiement=ModePaiement.MOBILE_MONEY,
            reference_transaction="TXN-99999",
            enregistre_par="user-001",
        )
        self.assertEqual(paiement.reference_transaction, "TXN-99999")

    def test_facture_inconnue_leve_erreur(self) -> None:
        """Paiement sur une facture inexistante lève ObjectDoesNotExist."""
        with self.assertRaises(ObjectDoesNotExist):
            self.svc.enregistrer_paiement(
                facture_id="facture-inexistante",
                abonne_id="abonne-001",
                montant=50.00,
                date_paiement=date(2026, 6, 20),
                mode_paiement=ModePaiement.ESPECES,
                reference_transaction="",
                enregistre_par="user-001",
            )


class TestGetSolde(TestCase):
    """Tests de PaiementService.get_solde."""

    def setUp(self) -> None:
        self.svc = PaiementService()

    def test_get_solde_existant(self) -> None:
        """Récupère le solde d'une facture existante."""
        _creer_solde("facture-001")
        solde = self.svc.get_solde("facture-001")
        self.assertEqual(solde.facture_id, "facture-001")

    def test_get_solde_inexistant_leve_erreur(self) -> None:
        """Lève ObjectDoesNotExist pour une facture inconnue."""
        with self.assertRaises(ObjectDoesNotExist):
            self.svc.get_solde("facture-inexistante")


class TestListPaiements(TestCase):
    """Tests de PaiementService.list_paiements."""

    def setUp(self) -> None:
        self.svc = PaiementService()
        _creer_solde("facture-001", "abonne-001", 500.00)
        _creer_solde("facture-002", "abonne-002", 200.00)

    def test_list_paiements_par_facture(self) -> None:
        """Liste les paiements filtrés par facture."""
        self.svc.enregistrer_paiement(
            "facture-001",
            "abonne-001",
            100.0,
            date.today(),
            ModePaiement.ESPECES,
            "",
            "user-001",
        )
        self.svc.enregistrer_paiement(
            "facture-002",
            "abonne-002",
            50.0,
            date.today(),
            ModePaiement.ESPECES,
            "",
            "user-001",
        )
        result = self.svc.list_paiements(facture_id="facture-001")
        self.assertEqual(len(result), 1)
        self.assertEqual(result[0].facture_id, "facture-001")

    def test_list_paiements_sans_filtre(self) -> None:
        """Liste tous les paiements sans filtre."""
        self.svc.enregistrer_paiement(
            "facture-001",
            "abonne-001",
            100.0,
            date.today(),
            ModePaiement.ESPECES,
            "",
            "user-001",
        )
        self.svc.enregistrer_paiement(
            "facture-002",
            "abonne-002",
            50.0,
            date.today(),
            ModePaiement.ESPECES,
            "",
            "user-001",
        )
        result = self.svc.list_paiements()
        self.assertEqual(len(result), 2)


class TestListImpayes(TestCase):
    """Tests de PaiementService.list_impayes."""

    def setUp(self) -> None:
        self.svc = PaiementService()

    def test_list_impayes_retourne_factures_impayees(self) -> None:
        """Retourne les factures dont la date limite est dépassée et statut != PAYEE."""
        # Facture en retard impayée
        _creer_solde("facture-retard", date_limite=date.today() - timedelta(days=5))
        result = self.svc.list_impayes()
        self.assertEqual(len(result), 1)
        self.assertEqual(result[0].facture_id, "facture-retard")

    def test_list_impayes_ne_retourne_pas_factures_payees(self) -> None:
        """Ne retourne pas les factures PAYEE même si date dépassée."""
        _creer_solde("facture-payee", date_limite=date.today() - timedelta(days=5))
        # Payer la facture
        self.svc.enregistrer_paiement(
            "facture-payee",
            "abonne-001",
            300.0,
            date.today(),
            ModePaiement.ESPECES,
            "",
            "user-001",
        )
        result = self.svc.list_impayes()
        # La facture payée ne doit pas apparaître
        self.assertEqual(len(result), 0)

    def test_list_impayes_ne_retourne_pas_dans_les_delais(self) -> None:
        """Ne retourne pas les factures dont la date limite est dans le futur."""
        _creer_solde("facture-future", date_limite=date.today() + timedelta(days=5))
        result = self.svc.list_impayes()
        self.assertEqual(len(result), 0)

    def test_list_impayes_retourne_partielles(self) -> None:
        """Retourne les factures partiellement payées après la date limite."""
        _creer_solde("facture-partielle", date_limite=date.today() - timedelta(days=2))
        # Paiement partiel
        self.svc.enregistrer_paiement(
            "facture-partielle",
            "abonne-001",
            100.0,
            date.today(),
            ModePaiement.ESPECES,
            "",
            "user-001",
        )
        result = self.svc.list_impayes()
        self.assertEqual(len(result), 1)
        self.assertEqual(result[0].statut, StatutSolde.PARTIELLE)


class TestSuiviImpaye(TestCase):
    """Tests du suivi des impayés."""

    def setUp(self) -> None:
        self.svc = PaiementService()
        # Créer une facture en retard
        _creer_solde(
            "facture-impayee",
            date_limite=date.today() - timedelta(days=10),
        )
        # Créer manuellement un suivi impayé
        self.suivi = SuiviImpaye.objects.create(
            facture_id="facture-impayee",
            abonne_id="abonne-001",
            date_depassement=date.today() - timedelta(days=10),
        )

    def test_get_suivi_impaye_existant(self) -> None:
        """Récupère le suivi d'une facture impayée existante."""
        suivi = self.svc.get_suivi_impaye("facture-impayee")
        self.assertEqual(suivi.facture_id, "facture-impayee")
        self.assertEqual(suivi.etape_actuelle, 1)

    def test_get_suivi_impaye_inexistant_leve_erreur(self) -> None:
        """Lève ObjectDoesNotExist si aucun suivi n'existe pour la facture."""
        with self.assertRaises(ObjectDoesNotExist):
            self.svc.get_suivi_impaye("facture-sans-suivi")

    def test_marquer_facture_payee_resout_suivi(self) -> None:
        """Après paiement total, le suivi est résolu (resolu_le = today)."""
        solde = SoldeFacture.objects.get(pk="facture-impayee")
        solde.statut = StatutSolde.PAYEE
        solde.save()

        with patch("paiements.services.NotificationServiceClient") as mock_cls:
            mock_cls.return_value.envoyer_relance = MagicMock()
            self.svc.marquer_facture_payee_si_applicable(solde)

        self.suivi.refresh_from_db()
        self.assertEqual(self.suivi.resolu_le, date.today())

    def test_suspendre_relances_apres_paiement_partiel(self) -> None:
        """Après paiement partiel, les relances sont suspendues N jours."""
        from datetime import timedelta

        solde = SoldeFacture.objects.get(pk="facture-impayee")
        solde.statut = StatutSolde.PARTIELLE
        solde.save()

        self.svc.suspendre_relances_si_partiel(solde, jours_suspension=5)

        self.suivi.refresh_from_db()
        expected = date.today() + timedelta(days=5)
        self.assertEqual(self.suivi.relances_suspendues_jusqu, expected)


class TestImpayeService(TestCase):
    """Tests du service d'escalade des impayés."""

    def setUp(self) -> None:
        self.svc = ImpayeService()

    @patch("paiements.services.NotificationServiceClient")
    @patch("paiements.services.AbonneServiceClient")
    @patch("paiements.services.ConfigServiceClient")
    def test_verifier_et_escalader_envoie_rappel_1(
        self, mock_config_cls, mock_abonne_cls, mock_notif_cls
    ) -> None:
        """Envoie le 1er rappel pour une facture dépassée depuis J+0."""
        mock_config_cls.return_value.get_delais_impayes.return_value = {
            "rappel_1": 0,
            "rappel_2": 3,
            "avertissement": 7,
            "suspension": 10,
            "suspension_auto": True,
            "suspension_relances": 5,
        }
        mock_notif = MagicMock()
        mock_notif_cls.return_value = mock_notif
        mock_abonne_cls.return_value = MagicMock()

        # Facture en retard depuis 1 jour
        _creer_solde("facture-j1", date_limite=date.today() - timedelta(days=1))

        self.svc.verifier_et_escalader()

        mock_notif.envoyer_relance.assert_called()
        suivi = SuiviImpaye.objects.get(facture_id="facture-j1")
        self.assertTrue(suivi.rappel_1_envoye)

    @patch("paiements.services.NotificationServiceClient")
    @patch("paiements.services.AbonneServiceClient")
    @patch("paiements.services.ConfigServiceClient")
    def test_verifier_et_escalader_etape_4_suspend_abonne(
        self, mock_config_cls, mock_abonne_cls, mock_notif_cls
    ) -> None:
        """Suspend l'abonné à l'étape 4 (J+10)."""
        mock_config_cls.return_value.get_delais_impayes.return_value = {
            "rappel_1": 0,
            "rappel_2": 3,
            "avertissement": 7,
            "suspension": 10,
            "suspension_auto": True,
            "suspension_relances": 5,
        }
        mock_notif = MagicMock()
        mock_notif_cls.return_value = mock_notif
        mock_abonne = MagicMock()
        mock_abonne_cls.return_value = mock_abonne

        # Facture en retard depuis 11 jours
        _creer_solde("facture-j11", date_limite=date.today() - timedelta(days=11))

        self.svc.verifier_et_escalader()

        mock_abonne.suspendre_abonne.assert_called_once_with("abonne-001")
        suivi = SuiviImpaye.objects.get(facture_id="facture-j11")
        self.assertTrue(suivi.suspension_effectuee)

    @patch("paiements.services.NotificationServiceClient")
    @patch("paiements.services.AbonneServiceClient")
    @patch("paiements.services.ConfigServiceClient")
    def test_verifier_et_escalader_skip_si_relances_suspendues(
        self, mock_config_cls, mock_abonne_cls, mock_notif_cls
    ) -> None:
        """Skip les relances si relances_suspendues_jusqu > today."""
        mock_config_cls.return_value.get_delais_impayes.return_value = {
            "rappel_1": 0,
            "rappel_2": 3,
            "avertissement": 7,
            "suspension": 10,
            "suspension_auto": True,
            "suspension_relances": 5,
        }
        mock_notif = MagicMock()
        mock_notif_cls.return_value = mock_notif
        mock_abonne_cls.return_value = MagicMock()

        _creer_solde("facture-suspendue", date_limite=date.today() - timedelta(days=5))
        # Créer un suivi avec relances suspendues jusqu'à demain
        SuiviImpaye.objects.create(
            facture_id="facture-suspendue",
            abonne_id="abonne-001",
            date_depassement=date.today() - timedelta(days=5),
            relances_suspendues_jusqu=date.today() + timedelta(days=1),
        )

        self.svc.verifier_et_escalader()

        # Aucune relance ne doit être envoyée
        mock_notif.envoyer_relance.assert_not_called()
