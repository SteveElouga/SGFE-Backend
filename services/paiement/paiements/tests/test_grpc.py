"""Tests du serveur gRPC du Paiement Service.

Les servicers ne gèrent plus les erreurs eux-mêmes : le mapping exception ->
code gRPC est centralisé dans ErrorHandlingInterceptor (testé dans
test_grpc_interceptors.py). Les tests ci-dessous vérifient donc que le
servicer **propage** l'exception métier attendue.
"""

import sys
from datetime import date, timedelta
from decimal import Decimal
from pathlib import Path
from unittest.mock import MagicMock, patch

import grpc
from django.conf import settings
from django.core.exceptions import ObjectDoesNotExist, ValidationError
from django.test import TestCase

sys.path.insert(0, str(Path(settings.BASE_DIR) / "proto"))

import paiement_service_pb2 as pb

from paiements.grpc_server import PaiementServicer
from paiements.models import AvoirAbonne, ModePaiement, SoldeFacture, StatutSolde
from paiements.repositories import PaiementRepository, SoldeFactureRepository
from paiements.services import PaiementService


def _mock_context() -> MagicMock:
    """Contexte gRPC mocké (l'abort est fait par l'interceptor, pas le servicer)."""
    return MagicMock(spec=grpc.ServicerContext)


def _creer_solde(
    facture_id: str = "facture-001",
    abonne_id: str = "abonne-001",
    montant: float = 300.00,
    date_limite: date | None = None,
    campagne_id: str = "",
) -> SoldeFacture:
    """Crée un SoldeFacture de test."""
    return SoldeFactureRepository().create(
        facture_id=facture_id,
        abonne_id=abonne_id,
        montant_total=Decimal(str(montant)),
        date_limite_paiement=date_limite or date(2026, 7, 31),
        campagne_id=campagne_id,
    )


class TestInitialiserSoldeRPC(TestCase):
    """Tests du RPC InitialiserSolde."""

    def setUp(self) -> None:
        with patch("paiements.grpc_server.FacturationServiceClient"):
            self.servicer = PaiementServicer()

    def test_initialiser_solde_succes(self) -> None:
        """InitialiserSolde crée un SoldeFacture et retourne SoldeResponse."""
        request = pb.InitialiserSoldeRequest(
            facture_id="facture-001",
            abonne_id="abonne-001",
            montant_total=300.00,
            date_limite_paiement="2026-07-31",
        )
        response = self.servicer.InitialiserSolde(request, _mock_context())
        self.assertEqual(response.facture_id, "facture-001")
        self.assertEqual(response.statut, StatutSolde.IMPAYEE)
        self.assertAlmostEqual(response.montant_total, 300.00)
        self.assertAlmostEqual(response.montant_paye, 0.0)

    def test_initialiser_solde_stocke_campagne_id(self) -> None:
        request = pb.InitialiserSoldeRequest(
            facture_id="facture-camp",
            abonne_id="abonne-001",
            montant_total=300.00,
            date_limite_paiement="2026-07-31",
            campagne_id="camp-42",
        )
        self.servicer.InitialiserSolde(request, _mock_context())
        self.assertEqual(SoldeFacture.objects.get(facture_id="facture-camp").campagne_id, "camp-42")

    def test_initialiser_solde_montant_nul_propage_validation_error(self) -> None:
        """Un montant nul propage une ValidationError (-> INVALID_ARGUMENT via interceptor)."""
        request = pb.InitialiserSoldeRequest(
            facture_id="facture-002",
            abonne_id="abonne-001",
            montant_total=0.0,
            date_limite_paiement="2026-07-31",
        )
        with self.assertRaises(ValidationError):
            self.servicer.InitialiserSolde(request, _mock_context())

    def test_initialiser_solde_date_invalide_propage_value_error(self) -> None:
        """Une date mal formatée propage une ValueError (-> INVALID_ARGUMENT via interceptor)."""
        request = pb.InitialiserSoldeRequest(
            facture_id="facture-003",
            abonne_id="abonne-001",
            montant_total=100.00,
            date_limite_paiement="pas-une-date",
        )
        with self.assertRaises(ValueError):
            self.servicer.InitialiserSolde(request, _mock_context())


class TestInitialiserSoldeAvecAvoirRPC(TestCase):
    """Tests de la propagation quand un avoir s'impute à la création d'une facture.

    `_appliquer_avoir` (appelée dans la même transaction qu'`initialiser_solde`)
    peut immédiatement partiellement ou totalement soldé une facture toute
    neuve. Ces tests vérifient que `InitialiserSolde` en tire les mêmes
    conséquences que `_propager_versement` en tire d'un encaissement direct."""

    @patch("paiements.grpc_server.publish_reporting_event")
    @patch("paiements.grpc_server.FacturationServiceClient")
    def test_avoir_suffisant_solde_la_facture_et_sync_facturation(
        self, mock_fact_cls: MagicMock, mock_pub: MagicMock
    ) -> None:
        AvoirAbonne.objects.create(abonne_id="abonne-avoir", montant=Decimal("500.00"))
        servicer = PaiementServicer()

        response = servicer.InitialiserSolde(
            pb.InitialiserSoldeRequest(
                facture_id="facture-avoir-total",
                abonne_id="abonne-avoir",
                montant_total=300.00,
                date_limite_paiement="2026-07-31",
                campagne_id="camp-avoir",
            ),
            _mock_context(),
        )

        self.assertEqual(response.statut, StatutSolde.PAYEE)
        mock_fact_cls.return_value.update_statut_facture.assert_called_once_with(
            facture_id="facture-avoir-total", statut=StatutSolde.PAYEE
        )

        types_publies = [c.kwargs["type_update"] for c in mock_pub.call_args_list]
        self.assertIn("PAIEMENT", types_publies)
        self.assertIn("IMPAYE_RESOLU", types_publies)
        paiement_call = next(c for c in mock_pub.call_args_list if c.kwargs["type_update"] == "PAIEMENT")
        self.assertAlmostEqual(paiement_call.kwargs["montant_paiement"], 300.00)
        self.assertEqual(paiement_call.kwargs["campagne_id"], "camp-avoir")

    @patch("paiements.grpc_server.publish_reporting_event")
    @patch("paiements.grpc_server.FacturationServiceClient")
    def test_avoir_partiel_sync_facturation_sans_impaye_resolu(
        self, mock_fact_cls: MagicMock, mock_pub: MagicMock
    ) -> None:
        """Un avoir qui ne couvre qu'une partie laisse la facture PARTIELLE :
        toujours à synchroniser, mais sans le déclencheur de rétablissement
        (aucune dette éteinte)."""
        AvoirAbonne.objects.create(abonne_id="abonne-avoir-partiel", montant=Decimal("100.00"))
        servicer = PaiementServicer()

        response = servicer.InitialiserSolde(
            pb.InitialiserSoldeRequest(
                facture_id="facture-avoir-partiel",
                abonne_id="abonne-avoir-partiel",
                montant_total=300.00,
                date_limite_paiement="2026-07-31",
                campagne_id="camp-avoir",
            ),
            _mock_context(),
        )

        self.assertEqual(response.statut, StatutSolde.PARTIELLE)
        mock_fact_cls.return_value.update_statut_facture.assert_called_once_with(
            facture_id="facture-avoir-partiel", statut=StatutSolde.PARTIELLE
        )
        types_publies = [c.kwargs["type_update"] for c in mock_pub.call_args_list]
        self.assertEqual(types_publies, ["PAIEMENT"])

    @patch("paiements.grpc_server.FacturationServiceClient")
    def test_sans_avoir_aucune_propagation(self, mock_fact_cls: MagicMock) -> None:
        """Le cas courant (pas d'avoir) ne doit rien changer : pas d'appel
        Facturation superflu sur une facture qui vient de naître IMPAYÉE."""
        servicer = PaiementServicer()

        servicer.InitialiserSolde(
            pb.InitialiserSoldeRequest(
                facture_id="facture-sans-avoir",
                abonne_id="abonne-sans-avoir",
                montant_total=300.00,
                date_limite_paiement="2026-07-31",
            ),
            _mock_context(),
        )

        mock_fact_cls.return_value.update_statut_facture.assert_not_called()

    @patch("paiements.services.NotificationServiceClient")
    @patch("paiements.services.AbonneServiceClient")
    @patch("paiements.grpc_server.publish_reporting_event")
    @patch("paiements.grpc_server.FacturationServiceClient")
    def test_avoir_qui_eteint_la_dette_totale_retablit_l_abonne_suspendu(
        self, mock_fact_cls: MagicMock, mock_pub: MagicMock, mock_abonne_cls: MagicMock, mock_notif_cls: MagicMock
    ) -> None:
        """Un abonné suspendu dont l'avoir couvre entièrement cette nouvelle
        facture — et donc sa dette totale — doit être rétabli. C'était le seul
        chemin de `retablir_si_dette_eteinte` jamais atteint par cette
        imputation : un abonné qui ne devait plus rien restait coupé."""
        AvoirAbonne.objects.create(abonne_id="abonne-suspendu", montant=Decimal("300.00"))
        mock_abonne_cls.return_value.reactiver_abonne.return_value = True
        servicer = PaiementServicer()

        servicer.InitialiserSolde(
            pb.InitialiserSoldeRequest(
                facture_id="facture-reactivation",
                abonne_id="abonne-suspendu",
                montant_total=300.00,
                date_limite_paiement="2026-07-31",
            ),
            _mock_context(),
        )

        mock_abonne_cls.return_value.reactiver_abonne.assert_called_once_with("abonne-suspendu")
        mock_notif_cls.return_value.envoyer_relance.assert_called_once_with(
            facture_id="facture-reactivation", abonne_id="abonne-suspendu", etape=0
        )


class TestEnregistrerPaiementRPC(TestCase):
    """Tests du RPC EnregistrerPaiement."""

    def setUp(self) -> None:
        with patch("paiements.grpc_server.FacturationServiceClient"):
            self.servicer = PaiementServicer()
        _creer_solde("facture-001", "abonne-001", 300.00)

    @patch("paiements.grpc_server.FacturationServiceClient")
    def test_enregistrer_paiement_succes(self, mock_fact_cls: MagicMock) -> None:
        """EnregistrerPaiement retourne un PaiementResponse valide."""
        mock_fact_cls.return_value.update_statut_facture = MagicMock()
        with patch("paiements.grpc_server.FacturationServiceClient"):
            servicer = PaiementServicer()
        request = pb.EnregistrerPaiementRequest(
            facture_id="facture-001",
            abonne_id="abonne-001",
            montant=100.00,
            date_paiement="2026-06-20",
            mode_paiement="ESPECES",
            reference_transaction="",
            enregistre_par="user-001",
        )
        response = servicer.EnregistrerPaiement(request, _mock_context())
        self.assertIsNotNone(response.paiement_id)
        self.assertEqual(response.facture_id, "facture-001")
        self.assertAlmostEqual(response.montant, 100.00)
        self.assertEqual(response.enregistre_par, "user-001")

    @patch("paiements.grpc_server.NotificationServiceClient")
    @patch("paiements.grpc_server.FacturationServiceClient")
    def test_enregistrer_paiement_declenche_envoi_recu(
        self, mock_fact_cls: MagicMock, mock_notif_cls: MagicMock
    ) -> None:
        """Après enregistrement, le reçu part automatiquement à l'abonné (WhatsApp)."""
        _creer_solde("facture-recu", "abonne-001", 300.00)
        servicer = PaiementServicer()
        request = pb.EnregistrerPaiementRequest(
            facture_id="facture-recu",
            abonne_id="abonne-001",
            montant=120.00,
            date_paiement="2026-06-20",
            mode_paiement="MOBILE_MONEY",
            reference_transaction="MM-1",
            enregistre_par="user-001",
        )
        response = servicer.EnregistrerPaiement(request, _mock_context())

        mock_notif_cls.return_value.envoyer_recu.assert_called_once()
        kwargs = mock_notif_cls.return_value.envoyer_recu.call_args.kwargs
        self.assertEqual(kwargs["paiement_id"], response.paiement_id)
        self.assertEqual(kwargs["facture_id"], "facture-recu")
        self.assertEqual(kwargs["abonne_id"], "abonne-001")
        self.assertAlmostEqual(kwargs["montant"], 120.00)

        # Le reçu annonce la dette TOTALE, pas le reste de la seule facture visée.
        #
        # Cet abonné doit deux factures de 300 (celle du setUp et celle-ci) et
        # verse 120 : il doit encore 480. L'ancienne valeur attendue était 180 —
        # le reste de cette facture-là — et c'est justement le défaut. Un reçu qui
        # annonce « solde restant dû : 180 » à quelqu'un qui doit 480 fait dire au
        # document l'inverse de la vérité, et c'est le document que l'abonné garde.
        #
        # Même décision que côté frontend en #95 : dire ce que l'abonné doit
        # ailleurs, plutôt que de le taire.
        self.assertAlmostEqual(kwargs["solde_restant"], 480.00)  # 600 dus - 120 versés

    @patch("paiements.grpc_server.publish_reporting_event")
    @patch("paiements.grpc_server.FacturationServiceClient")
    def test_enregistrer_paiement_publie_stats_reporting(self, mock_fact_cls: MagicMock, mock_pub: MagicMock) -> None:
        _creer_solde("facture-rep", "abonne-001", 300.00, campagne_id="camp-9")
        servicer = PaiementServicer()
        request = pb.EnregistrerPaiementRequest(
            facture_id="facture-rep",
            abonne_id="abonne-001",
            montant=100.00,
            date_paiement="2026-06-20",
            mode_paiement="ESPECES",
            reference_transaction="",
            enregistre_par="user-001",
        )
        servicer.EnregistrerPaiement(request, _mock_context())

        mock_pub.assert_called()
        args, kwargs = mock_pub.call_args_list[0]
        self.assertEqual(args[0], "PAIEMENT_STATS")
        self.assertEqual(kwargs["campagne_id"], "camp-9")
        self.assertEqual(kwargs["type_update"], "PAIEMENT")
        self.assertAlmostEqual(kwargs["montant_paiement"], 100.0)

    @patch("paiements.grpc_server.publish_reporting_event")
    @patch("paiements.grpc_server.FacturationServiceClient")
    def test_enregistrer_paiement_total_emet_impaye_resolu(self, mock_fact_cls: MagicMock, mock_pub: MagicMock) -> None:
        _creer_solde("facture-full", "abonne-001", 100.00, campagne_id="camp-9")
        servicer = PaiementServicer()
        request = pb.EnregistrerPaiementRequest(
            facture_id="facture-full",
            abonne_id="abonne-001",
            montant=100.00,  # solde entièrement payé -> PAYEE
            date_paiement="2026-06-20",
            mode_paiement="ESPECES",
            reference_transaction="",
            enregistre_par="user-001",
        )
        servicer.EnregistrerPaiement(request, _mock_context())

        types = [c.kwargs["type_update"] for c in mock_pub.call_args_list]
        self.assertIn("PAIEMENT", types)
        self.assertIn("IMPAYE_RESOLU", types)

    def test_enregistrer_paiement_montant_invalide_propage_validation_error(self) -> None:
        request = pb.EnregistrerPaiementRequest(
            facture_id="facture-001",
            abonne_id="abonne-001",
            montant=0.0,
            date_paiement="2026-06-20",
            mode_paiement="ESPECES",
            reference_transaction="",
            enregistre_par="user-001",
        )
        with self.assertRaises(ValidationError):
            self.servicer.EnregistrerPaiement(request, _mock_context())

    def test_enregistrer_paiement_surpaiement_accepte_et_credite_avoir(self) -> None:
        """Un surpaiement est accepté (facture soldée) et l'excédent est porté
        au crédit (avoir) de l'abonné — plus de ValidationError."""
        from paiements.models import AvoirAbonne

        request = pb.EnregistrerPaiementRequest(
            facture_id="facture-001",
            abonne_id="abonne-001",
            montant=500.00,  # solde restant 300 → excédent 200
            date_paiement="2026-06-20",
            mode_paiement="ESPECES",
            reference_transaction="",
            enregistre_par="user-001",
        )
        response = self.servicer.EnregistrerPaiement(request, _mock_context())
        # La réponse porte la part imputée (300), pas la somme reçue (500) :
        # une écriture par facture touchée. Voir test_services pour le pourquoi
        # — l'ancienne sémantique double comptait les recettes.
        self.assertAlmostEqual(response.montant, 300.00)
        self.assertEqual(str(AvoirAbonne.objects.get(abonne_id="abonne-001").montant), "200.00")

    def test_enregistrer_paiement_facture_inconnue_propage_not_found(self) -> None:
        request = pb.EnregistrerPaiementRequest(
            facture_id="facture-inconnue",
            abonne_id="abonne-001",
            montant=100.00,
            date_paiement="2026-06-20",
            mode_paiement="ESPECES",
            reference_transaction="",
            enregistre_par="user-001",
        )
        with self.assertRaises(ObjectDoesNotExist):
            self.servicer.EnregistrerPaiement(request, _mock_context())

    def test_enregistrer_paiement_mobile_money_sans_reference_propage_validation_error(self) -> None:
        request = pb.EnregistrerPaiementRequest(
            facture_id="facture-001",
            abonne_id="abonne-001",
            montant=100.00,
            date_paiement="2026-06-20",
            mode_paiement="MOBILE_MONEY",
            reference_transaction="",
            enregistre_par="user-001",
        )
        with self.assertRaises(ValidationError):
            self.servicer.EnregistrerPaiement(request, _mock_context())


class TestAnnulerPaiementRPC(TestCase):
    """Tests du RPC AnnulerPaiement."""

    def setUp(self) -> None:
        with (
            patch("paiements.grpc_server.FacturationServiceClient"),
            patch("paiements.grpc_server.NotificationServiceClient"),
        ):
            self.servicer = PaiementServicer()

    def test_annuler_paiement_succes(self) -> None:
        _creer_solde("facture-ann", "abonne-001", 300.00)
        enreg = self.servicer.EnregistrerPaiement(
            pb.EnregistrerPaiementRequest(
                facture_id="facture-ann",
                abonne_id="abonne-001",
                montant=100.00,
                date_paiement="2026-06-20",
                mode_paiement="ESPECES",
                reference_transaction="",
                enregistre_par="user-001",
            ),
            _mock_context(),
        )

        response = self.servicer.AnnulerPaiement(
            pb.AnnulerPaiementRequest(paiement_id=enreg.paiement_id, motif="erreur de saisie", annule_par="admin-1"),
            _mock_context(),
        )

        self.assertEqual(response.paiement_id, enreg.paiement_id)
        self.assertTrue(response.annule)

    @patch("paiements.grpc_server.publish_reporting_event")
    @patch("paiements.grpc_server.NotificationServiceClient")
    @patch("paiements.grpc_server.FacturationServiceClient")
    def test_annuler_paiement_versement_cascade_resynchronise_toutes_les_factures(
        self, mock_fact_cls: MagicMock, mock_notif_cls: MagicMock, mock_pub: MagicMock
    ) -> None:
        """Un versement qui a soldé deux factures d'un coup : l'annuler doit
        resynchroniser LES DEUX, pas seulement celle sur laquelle on a cliqué —
        même défaut que celui déjà corrigé côté encaissement pour
        `EnregistrerPaiementAbonne` (voir `_propager_versement`)."""
        _creer_solde("facture-casc-1", "abonne-casc", 200.00, date(2026, 6, 30), "camp-casc")
        _creer_solde("facture-casc-2", "abonne-casc", 150.00, date(2026, 7, 31), "camp-casc")
        servicer = PaiementServicer()

        encaissement = servicer.EnregistrerPaiementAbonne(
            pb.EnregistrerPaiementAbonneRequest(
                abonne_id="abonne-casc",
                montant=350.00,  # exactement les deux factures, sans excédent
                date_paiement="2026-06-20",
                mode_paiement="ESPECES",
                reference_transaction="",
                enregistre_par="user-001",
            ),
            _mock_context(),
        )
        self.assertEqual(len(encaissement.paiements), 2)

        # Ce qui compte est ce que l'ANNULATION déclenche, pas l'encaissement
        # qui l'a précédée dans le même test.
        mock_fact_cls.return_value.update_statut_facture.reset_mock()
        mock_pub.reset_mock()
        mock_notif_cls.return_value.envoyer_relance.reset_mock()

        servicer.AnnulerPaiement(
            pb.AnnulerPaiementRequest(
                paiement_id=encaissement.paiements[0].paiement_id,
                motif="erreur de saisie",
                annule_par="admin-1",
            ),
            _mock_context(),
        )

        factures_sync = {
            c.kwargs["facture_id"] for c in mock_fact_cls.return_value.update_statut_facture.call_args_list
        }
        self.assertEqual(factures_sync, {"facture-casc-1", "facture-casc-2"})

        types_publies = [c.kwargs["type_update"] for c in mock_pub.call_args_list]
        self.assertEqual(types_publies, ["PAIEMENT_ANNULE", "PAIEMENT_ANNULE"])

        factures_relancees = {
            c.kwargs["facture_id"] for c in mock_notif_cls.return_value.envoyer_relance.call_args_list
        }
        self.assertEqual(factures_relancees, {"facture-casc-1", "facture-casc-2"})
        etapes = {c.kwargs["etape"] for c in mock_notif_cls.return_value.envoyer_relance.call_args_list}
        self.assertEqual(etapes, {5})


class TestAnnulerSoldeRPC(TestCase):
    """Tests du RPC AnnulerSolde (annulation d'une FACTURE, pas d'un paiement)."""

    @patch("paiements.grpc_server.publish_reporting_event")
    @patch("paiements.grpc_server.NotificationServiceClient")
    @patch("paiements.grpc_server.FacturationServiceClient")
    def test_annuler_solde_avec_versement_decremente_reporting_et_notifie(
        self, mock_fact_cls: MagicMock, mock_notif_cls: MagicMock, mock_pub: MagicMock
    ) -> None:
        """Ce qui avait déjà été versé sur cette facture était compté en
        recette : l'annuler doit décrémenter Reporting (comme
        `AnnulerPaiement` le fait déjà) et prévenir l'abonné, qui détient un
        reçu pour une facture qui n'existe plus."""
        _creer_solde("facture-annulee-versee", "abonne-001", 300.00, campagne_id="camp-ann")
        servicer = PaiementServicer()
        servicer.EnregistrerPaiement(
            pb.EnregistrerPaiementRequest(
                facture_id="facture-annulee-versee",
                abonne_id="abonne-001",
                montant=120.00,
                date_paiement="2026-06-20",
                mode_paiement="ESPECES",
                reference_transaction="",
                enregistre_par="user-001",
            ),
            _mock_context(),
        )
        mock_pub.reset_mock()
        mock_notif_cls.return_value.envoyer_relance.reset_mock()

        response = servicer.AnnulerSolde(
            pb.AnnulerSoldeRequest(facture_id="facture-annulee-versee", motif="erreur d'index"),
            _mock_context(),
        )

        self.assertAlmostEqual(response.montant_porte_en_avoir, 120.00)
        mock_pub.assert_called_once()
        _, kwargs = mock_pub.call_args
        self.assertEqual(kwargs["type_update"], "PAIEMENT_ANNULE")
        self.assertEqual(kwargs["campagne_id"], "camp-ann")
        self.assertAlmostEqual(kwargs["montant_paiement"], 120.00)

        mock_notif_cls.return_value.envoyer_relance.assert_called_once_with(
            facture_id="facture-annulee-versee", abonne_id="abonne-001", etape=5
        )

    @patch("paiements.grpc_server.publish_reporting_event")
    @patch("paiements.grpc_server.NotificationServiceClient")
    @patch("paiements.grpc_server.FacturationServiceClient")
    def test_annuler_solde_sans_versement_ne_decremente_rien_mais_previent_quand_meme(
        self, mock_fact_cls: MagicMock, mock_notif_cls: MagicMock, mock_pub: MagicMock
    ) -> None:
        """Rien n'a jamais été versé : rien à décrémenter côté Reporting, mais
        l'abonné doit être prévenu quand même — PDF déjà reçu, il croirait
        sinon cette facture toujours due. Étape 6, pas 5 : aucun versement à
        annuler ici."""
        _creer_solde("facture-annulee-vide", "abonne-001", 300.00, campagne_id="camp-ann")
        servicer = PaiementServicer()

        servicer.AnnulerSolde(
            pb.AnnulerSoldeRequest(facture_id="facture-annulee-vide", motif="erreur d'index"),
            _mock_context(),
        )

        mock_pub.assert_not_called()
        mock_notif_cls.return_value.envoyer_relance.assert_called_once_with(
            facture_id="facture-annulee-vide", abonne_id="abonne-001", etape=6
        )


class TestGetSoldeRPC(TestCase):
    """Tests du RPC GetSolde."""

    def setUp(self) -> None:
        with patch("paiements.grpc_server.FacturationServiceClient"):
            self.servicer = PaiementServicer()

    def test_get_solde_succes(self) -> None:
        """GetSolde retourne le solde d'une facture existante."""
        _creer_solde("facture-001")
        request = pb.FactureIdRequest(facture_id="facture-001")
        response = self.servicer.GetSolde(request, _mock_context())
        self.assertEqual(response.facture_id, "facture-001")
        self.assertEqual(response.statut, StatutSolde.IMPAYEE)

    def test_get_solde_facture_inconnue_propage_not_found(self) -> None:
        request = pb.FactureIdRequest(facture_id="facture-inconnue")
        with self.assertRaises(ObjectDoesNotExist):
            self.servicer.GetSolde(request, _mock_context())


class TestListPaiementsRPC(TestCase):
    """Tests du RPC ListPaiements."""

    def setUp(self) -> None:
        with patch("paiements.grpc_server.FacturationServiceClient"):
            self.servicer = PaiementServicer()
        _creer_solde("facture-001", "abonne-001", 500.00)

    def test_list_paiements_retourne_liste(self) -> None:
        """ListPaiements retourne les paiements de la facture."""
        svc = PaiementService()
        svc.enregistrer_paiement(
            "facture-001",
            "abonne-001",
            100.0,
            date.today(),
            ModePaiement.ESPECES,
            "",
            "user-001",
        )
        request = pb.ListPaiementsRequest(facture_id="facture-001", abonne_id="")
        response = self.servicer.ListPaiements(request, _mock_context())
        self.assertEqual(len(response.paiements), 1)
        self.assertEqual(response.paiements[0].facture_id, "facture-001")
        self.assertEqual(response.paiements[0].enregistre_par, "user-001")

    def test_list_paiements_vide_retourne_liste_vide(self) -> None:
        """ListPaiements retourne une liste vide si aucun paiement."""
        request = pb.ListPaiementsRequest(facture_id="facture-001", abonne_id="")
        response = self.servicer.ListPaiements(request, _mock_context())
        self.assertEqual(len(response.paiements), 0)

    def test_list_paiements_sans_pagination_total_zero_si_aucun_paiement(self) -> None:
        # Non-régression : `limit`/`offset` omis (champs proto3 `optional`
        # non définis) doit préserver le comportement historique.
        request = pb.ListPaiementsRequest(facture_id="facture-001", abonne_id="")
        self.assertEqual(self.servicer.ListPaiements(request, _mock_context()).total, 0)


class TestListPaiementsRPCPagination(TestCase):
    """`limit`/`offset` optionnels sur `ListPaiements` — rétrocompatibilité
    stricte (omis, comportement historique inchangé), combinaison avec les
    filtres existants, et `total` cohérent avec le nombre réel de lignes
    filtrées (pas la page rendue)."""

    def setUp(self) -> None:
        with patch("paiements.grpc_server.FacturationServiceClient"):
            self.servicer = PaiementServicer()
        _creer_solde("facture-001", "abonne-001", 500.00)
        for i in range(5):
            PaiementRepository().create(
                facture_id="facture-001",
                abonne_id="abonne-001",
                montant=Decimal("10"),
                date_paiement=date(2026, 7, 1 + i),
                mode_paiement=ModePaiement.ESPECES,
                reference_transaction="",
                enregistre_par="caissier",
            )

    def test_sans_pagination_renvoie_tout_et_total_coherent(self) -> None:
        request = pb.ListPaiementsRequest(facture_id="facture-001")
        response = self.servicer.ListPaiements(request, _mock_context())
        self.assertEqual(len(response.paiements), 5)
        self.assertEqual(response.total, 5)

    def test_avec_pagination_tronque_et_ordonne_chronologiquement(self) -> None:
        request = pb.ListPaiementsRequest(facture_id="facture-001", limit=2, offset=0)
        response = self.servicer.ListPaiements(request, _mock_context())
        self.assertEqual([p.date_paiement for p in response.paiements], ["2026-07-01", "2026-07-02"])
        # Le total porte sur l'ensemble filtré, pas sur la seule page rendue.
        self.assertEqual(response.total, 5)

    def test_pagination_hors_limites_renvoie_liste_vide_pas_une_erreur(self) -> None:
        request = pb.ListPaiementsRequest(facture_id="facture-001", limit=10, offset=100)
        response = self.servicer.ListPaiements(request, _mock_context())
        self.assertEqual(len(response.paiements), 0)
        self.assertEqual(response.total, 5)

    def test_pagination_se_combine_au_filtre_abonne(self) -> None:
        # Un paiement d'un autre abonné, hors filtre : ne doit compter ni
        # dans la page ni dans le total.
        _creer_solde("facture-002", "abonne-002", 500.00)
        PaiementRepository().create(
            facture_id="facture-002",
            abonne_id="abonne-002",
            montant=Decimal("10"),
            date_paiement=date(2026, 8, 1),
            mode_paiement=ModePaiement.ESPECES,
            reference_transaction="",
            enregistre_par="caissier",
        )
        request = pb.ListPaiementsRequest(abonne_id="abonne-001", limit=2, offset=0)
        response = self.servicer.ListPaiements(request, _mock_context())
        self.assertEqual(len(response.paiements), 2)
        self.assertEqual(response.total, 5)


class TestListPaiementsParCampagneRPC(TestCase):
    """Tests du RPC ListPaiementsParCampagne (export CSV écran 13)."""

    def setUp(self) -> None:
        with patch("paiements.grpc_server.FacturationServiceClient"):
            self.servicer = PaiementServicer()
        _creer_solde("fac-a1", "ab-1", 500.00, campagne_id="camp-A")
        _creer_solde("fac-b1", "ab-2", 200.00, campagne_id="camp-B")

    def test_filtre_les_paiements_de_la_campagne(self) -> None:
        svc = PaiementService()
        svc.enregistrer_paiement("fac-a1", "ab-1", 100.0, date.today(), ModePaiement.ESPECES, "", "u-1")
        svc.enregistrer_paiement("fac-b1", "ab-2", 50.0, date.today(), ModePaiement.ESPECES, "", "u-1")

        response = self.servicer.ListPaiementsParCampagne(pb.CampagneIdRequest(campagne_id="camp-A"), _mock_context())
        self.assertEqual(len(response.paiements), 1)
        self.assertEqual(response.paiements[0].facture_id, "fac-a1")
        self.assertEqual(response.paiements[0].abonne_id, "ab-1")

    def test_campagne_sans_paiement_retourne_vide(self) -> None:
        response = self.servicer.ListPaiementsParCampagne(pb.CampagneIdRequest(campagne_id="camp-A"), _mock_context())
        self.assertEqual(len(response.paiements), 0)


class TestListImpayesRPC(TestCase):
    """Tests du RPC ListImpayes."""

    def setUp(self) -> None:
        with patch("paiements.grpc_server.FacturationServiceClient"):
            self.servicer = PaiementServicer()

    def test_list_impayes_retourne_la_liste(self) -> None:
        """ListImpayes retourne les soldes en retard non payés."""
        _creer_solde("facture-retard", date_limite=date.today() - timedelta(days=3))
        request = pb.EmptyRequest()
        response = self.servicer.ListImpayes(request, _mock_context())
        self.assertEqual(len(response.impayes), 1)
        self.assertEqual(response.impayes[0].facture_id, "facture-retard")

    def test_list_impayes_vide_si_aucun_retard(self) -> None:
        """ListImpayes retourne une liste vide si toutes les factures sont dans les délais."""
        _creer_solde("facture-ok", date_limite=date.today() + timedelta(days=5))
        request = pb.EmptyRequest()
        response = self.servicer.ListImpayes(request, _mock_context())
        self.assertEqual(len(response.impayes), 0)

    def test_list_impayes_exclut_factures_payees(self) -> None:
        """ListImpayes exclut les factures dont le statut est PAYEE."""
        _creer_solde("facture-payee", date_limite=date.today() - timedelta(days=3))
        svc = PaiementService()
        svc.enregistrer_paiement(
            "facture-payee",
            "abonne-001",
            300.0,
            date.today(),
            ModePaiement.ESPECES,
            "",
            "user-001",
        )
        request = pb.EmptyRequest()
        response = self.servicer.ListImpayes(request, _mock_context())
        self.assertEqual(len(response.impayes), 0)


class TestGetSuiviImpayeRPC(TestCase):
    """Tests du RPC GetSuiviImpaye."""

    def setUp(self) -> None:
        with patch("paiements.grpc_server.FacturationServiceClient"):
            self.servicer = PaiementServicer()

    def test_get_suivi_existant(self) -> None:
        """GetSuiviImpaye retourne le suivi d'une facture impayée."""
        from paiements.models import SuiviImpaye

        SuiviImpaye.objects.create(
            facture_id="facture-suivi",
            abonne_id="abonne-001",
            date_depassement=date.today() - timedelta(days=5),
        )
        request = pb.FactureIdRequest(facture_id="facture-suivi")
        response = self.servicer.GetSuiviImpaye(request, _mock_context())
        self.assertEqual(response.facture_id, "facture-suivi")
        self.assertEqual(response.abonne_id, "abonne-001")
        self.assertEqual(response.etape_actuelle, 1)

    def test_get_suivi_inexistant_propage_not_found(self) -> None:
        request = pb.FactureIdRequest(facture_id="facture-sans-suivi")
        with self.assertRaises(ObjectDoesNotExist):
            self.servicer.GetSuiviImpaye(request, _mock_context())


class TestRevalidationRoleDefenseProfondeur(TestCase):
    """Défense en profondeur (voir docs/CONFORMITE_SOC2_OWASP.md §3.1 A01,
    plan de remédiation item #3) : `EnregistrerPaiement`, `AnnulerPaiement`
    et `EnregistrerPaiementAbonne` revalident le rôle de l'appelant à partir
    de l'identité propagée par la gateway (`get_caller()`), en plus du RBAC
    déjà appliqué côté gateway.

    Le compromis assumé (documenté sur `_revalider_role_paiement`) : ce
    filet ne bloque JAMAIS l'appel, même avec un mauvais rôle ou une
    identité absente — il se contente de journaliser un avertissement. Ces
    tests vérifient donc la présence du log, pas un rejet de l'appel.
    """

    def setUp(self) -> None:
        with (
            patch("paiements.grpc_server.FacturationServiceClient"),
            patch("paiements.grpc_server.NotificationServiceClient"),
        ):
            self.servicer = PaiementServicer()
        _creer_solde("facture-role", "abonne-role", 300.00)

    def _poser_identite(self, role: str) -> None:
        from paiements.grpc_interceptors import CallerIdentity, caller_identity

        jeton = caller_identity.set(CallerIdentity(user_id="u-1", username="testeur", role=role))
        self.addCleanup(caller_identity.reset, jeton)

    def _requete_paiement(self) -> pb.EnregistrerPaiementRequest:
        return pb.EnregistrerPaiementRequest(
            facture_id="facture-role",
            abonne_id="abonne-role",
            montant=100.00,
            date_paiement="2026-06-20",
            mode_paiement="ESPECES",
            reference_transaction="",
            enregistre_par="user-001",
        )

    @patch("paiements.grpc_server.logger")
    def test_enregistrer_paiement_role_autorise_passe_sans_avertissement_de_role(self, mock_logger: MagicMock) -> None:
        self._poser_identite("COMPTABLE")
        response = self.servicer.EnregistrerPaiement(self._requete_paiement(), _mock_context())
        self.assertTrue(response.paiement_id)
        for appel in mock_logger.warning.call_args_list:
            self.assertNotIn("hors de l'ensemble autorisé", appel.args[0])

    def test_enregistrer_paiement_role_non_autorise_journalise_un_avertissement_mais_passe(self) -> None:
        self._poser_identite("AGENT")
        with self.assertLogs("paiements.grpc_server", level="WARNING") as journaux:
            response = self.servicer.EnregistrerPaiement(self._requete_paiement(), _mock_context())
        self.assertTrue(response.paiement_id)  # jamais bloqué (voir docstring de la classe)
        trace = "\n".join(journaux.output)
        self.assertIn("EnregistrerPaiement", trace)
        self.assertIn("hors de l'ensemble autorisé", trace)
        self.assertIn("AGENT", trace)

    def test_enregistrer_paiement_sans_identite_reste_retrocompatible(self) -> None:
        """Aucune identité propagée (appel hors gateway, ou service-à-service
        légitime) : le comportement reste EXACTEMENT celui d'avant ce
        correctif — aucune exception, la réponse est renvoyée normalement."""
        response = self.servicer.EnregistrerPaiement(self._requete_paiement(), _mock_context())
        self.assertTrue(response.paiement_id)

    def test_annuler_paiement_role_non_autorise_journalise_un_avertissement_mais_passe(self) -> None:
        enreg = self.servicer.EnregistrerPaiement(self._requete_paiement(), _mock_context())
        self._poser_identite("SUPERVISEUR")
        with self.assertLogs("paiements.grpc_server", level="WARNING") as journaux:
            response = self.servicer.AnnulerPaiement(
                pb.AnnulerPaiementRequest(paiement_id=enreg.paiement_id, motif="erreur", annule_par="admin-1"),
                _mock_context(),
            )
        self.assertTrue(response.annule)
        trace = "\n".join(journaux.output)
        self.assertIn("AnnulerPaiement", trace)
        self.assertIn("hors de l'ensemble autorisé", trace)

    def test_enregistrer_paiement_abonne_role_non_autorise_journalise_un_avertissement_mais_passe(self) -> None:
        self._poser_identite("AGENT")
        with self.assertLogs("paiements.grpc_server", level="WARNING") as journaux:
            response = self.servicer.EnregistrerPaiementAbonne(
                pb.EnregistrerPaiementAbonneRequest(
                    abonne_id="abonne-role",
                    montant=100.00,
                    date_paiement="2026-06-20",
                    mode_paiement="ESPECES",
                    reference_transaction="",
                    enregistre_par="user-001",
                ),
                _mock_context(),
            )
        self.assertEqual(len(response.paiements), 1)
        trace = "\n".join(journaux.output)
        self.assertIn("EnregistrerPaiementAbonne", trace)
        self.assertIn("hors de l'ensemble autorisé", trace)

    def test_enregistrer_paiement_abonne_sans_identite_reste_retrocompatible(self) -> None:
        response = self.servicer.EnregistrerPaiementAbonne(
            pb.EnregistrerPaiementAbonneRequest(
                abonne_id="abonne-role",
                montant=100.00,
                date_paiement="2026-06-20",
                mode_paiement="ESPECES",
                reference_transaction="",
                enregistre_par="user-001",
            ),
            _mock_context(),
        )
        self.assertEqual(len(response.paiements), 1)
