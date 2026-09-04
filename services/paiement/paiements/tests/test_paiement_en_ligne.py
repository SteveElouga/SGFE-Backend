"""Paiement en ligne (espace abonné public) — mode sandbox/mock exclusivement.

Relance de la décision §10.2 de l'audit, qui l'avait écartée. Ce fichier
couvre le modèle `SessionPaiementEnLigne`, le mock de passerelle
(`passerelle_paiement.py`) et les deux RPC (`CreerSessionPaiementEnLigne`,
`ConfirmerSessionPaiementEnLigne`) — création, confirmation réussie,
anti-IDOR (token invalide/différent), idempotence du rejeu, et expiration.
"""

import sys
import uuid
from datetime import date, timedelta
from decimal import Decimal
from pathlib import Path
from unittest.mock import MagicMock, patch

import grpc
from django.conf import settings
from django.core.exceptions import ObjectDoesNotExist, ValidationError
from django.test import SimpleTestCase, TestCase
from django.utils import timezone

sys.path.insert(0, str(Path(settings.BASE_DIR) / "proto"))

import paiement_service_pb2 as pb

from paiements.grpc_server import SYSTEME_PAIEMENT_EN_LIGNE, PaiementServicer
from paiements.models import (
    ModePaiement,
    Paiement,
    SessionPaiementEnLigne,
    StatutSessionPaiement,
)
from paiements.passerelle_paiement import MockPasserellePaiementClient
from paiements.repositories import SessionPaiementRepository, SoldeFactureRepository

ABONNE = "abonne-en-ligne"
FACTURE = "facture-en-ligne"


def _contexte() -> MagicMock:
    return MagicMock(spec=grpc.ServicerContext)


def _session(
    facture_id: str = FACTURE,
    abonne_id: str = ABONNE,
    montant: float = 5000,
    token_espace: str = "token-abonne-1",
    expire_dans_minutes: float = 30,
) -> SessionPaiementEnLigne:
    return SessionPaiementRepository().create(
        facture_id=facture_id,
        abonne_id=abonne_id,
        montant=Decimal(str(montant)),
        token_espace=token_espace,
        expire_a=timezone.now() + timedelta(minutes=expire_dans_minutes),
    )


class TestSessionPaiementEnLigneModel(TestCase):
    """Tests du modèle `SessionPaiementEnLigne`."""

    def test_creation_valeurs_par_defaut(self) -> None:
        session = _session()
        self.assertIsInstance(session.id, uuid.UUID)
        self.assertEqual(session.statut, StatutSessionPaiement.EN_ATTENTE)
        self.assertEqual(session.facture_id, FACTURE)
        self.assertEqual(session.abonne_id, ABONNE)
        self.assertEqual(session.montant, Decimal("5000"))
        self.assertEqual(session.token_espace, "token-abonne-1")
        self.assertIsNotNone(session.created_at)
        self.assertIsNotNone(session.expire_a)

    def test_statut_choices(self) -> None:
        self.assertEqual(StatutSessionPaiement.EN_ATTENTE, "EN_ATTENTE")
        self.assertEqual(StatutSessionPaiement.CONFIRMEE, "CONFIRMEE")
        self.assertEqual(StatutSessionPaiement.ECHOUEE, "ECHOUEE")
        self.assertEqual(StatutSessionPaiement.EXPIREE, "EXPIREE")

    def test_session_str(self) -> None:
        session = _session()
        self.assertIn(str(session.id), str(session))
        self.assertIn(StatutSessionPaiement.EN_ATTENTE, str(session))

    def test_id_reutilisable_comme_reference_transaction_paiement(self) -> None:
        """L'id de la session doit pouvoir devenir `Paiement.reference_transaction`
        tel quel — c'est tout le mécanisme d'idempotence de la confirmation."""
        session = _session()
        Paiement.objects.create(
            facture_id=FACTURE,
            abonne_id=ABONNE,
            montant=Decimal("5000"),
            date_paiement=date.today(),
            mode_paiement=ModePaiement.MOBILE_MONEY,
            reference_transaction=str(session.id),
            enregistre_par=SYSTEME_PAIEMENT_EN_LIGNE,
        )
        self.assertEqual(Paiement.objects.filter(reference_transaction=str(session.id)).count(), 1)


class TestMockPasserellePaiementClient(SimpleTestCase):
    """Tests du mock de passerelle — seule implémentation existante."""

    def test_creer_session_renvoie_l_url_du_contrat_frontend(self) -> None:
        mock = MockPasserellePaiementClient("token-abc")
        url = mock.creer_session(Decimal("1000"), "session-xyz")
        self.assertEqual(url, f"{settings.FRONTEND_URL}/espace/token-abc/paiement/session-xyz/confirmer")

    def test_confirmer_renvoie_toujours_true(self) -> None:
        mock = MockPasserellePaiementClient("token-abc")
        self.assertTrue(mock.confirmer("n-importe-quelle-reference"))
        self.assertTrue(mock.confirmer(""))


@patch("paiements.grpc_server.NotificationServiceClient")
class TestCreerSessionPaiementEnLigneRPC(TestCase):
    """Tests du RPC `CreerSessionPaiementEnLigne`."""

    def _requete(
        self, facture_id: str = FACTURE, montant: float = 5000, token_espace: str = "token-1"
    ) -> pb.CreerSessionPaiementRequest:
        return pb.CreerSessionPaiementRequest(facture_id=facture_id, montant=montant, token_espace=token_espace)

    def test_session_creee_avec_abonne_resolu_depuis_le_token(self, mock_notif: MagicMock) -> None:
        mock_notif.return_value.valider_token.return_value = {"is_valid": True, "abonne_id": ABONNE}
        servicer = PaiementServicer()

        reponse = servicer.CreerSessionPaiementEnLigne(self._requete(), _contexte())

        session = SessionPaiementEnLigne.objects.get(pk=reponse.session_id)
        self.assertEqual(session.abonne_id, ABONNE)
        self.assertEqual(session.facture_id, FACTURE)
        self.assertEqual(session.montant, Decimal("5000"))
        self.assertEqual(session.token_espace, "token-1")
        self.assertEqual(session.statut, StatutSessionPaiement.EN_ATTENTE)

    def test_reponse_porte_l_url_de_redirection_du_contrat(self, mock_notif: MagicMock) -> None:
        mock_notif.return_value.valider_token.return_value = {"is_valid": True, "abonne_id": ABONNE}
        servicer = PaiementServicer()

        reponse = servicer.CreerSessionPaiementEnLigne(self._requete(token_espace="token-xyz"), _contexte())

        self.assertEqual(
            reponse.url_redirection,
            f"{settings.FRONTEND_URL}/espace/token-xyz/paiement/{reponse.session_id}/confirmer",
        )
        self.assertEqual(reponse.statut, StatutSessionPaiement.EN_ATTENTE)
        self.assertTrue(reponse.expire_a)

    def test_expire_a_est_30_minutes_apres_la_creation(self, mock_notif: MagicMock) -> None:
        mock_notif.return_value.valider_token.return_value = {"is_valid": True, "abonne_id": ABONNE}
        servicer = PaiementServicer()

        avant = timezone.now()
        reponse = servicer.CreerSessionPaiementEnLigne(self._requete(), _contexte())

        session = SessionPaiementEnLigne.objects.get(pk=reponse.session_id)
        ecart = session.expire_a - avant
        self.assertAlmostEqual(ecart.total_seconds(), timedelta(minutes=30).total_seconds(), delta=5)

    def test_token_invalide_rejete(self, mock_notif: MagicMock) -> None:
        mock_notif.return_value.valider_token.return_value = {"is_valid": False, "abonne_id": ""}
        servicer = PaiementServicer()

        with self.assertRaises(ObjectDoesNotExist):
            servicer.CreerSessionPaiementEnLigne(self._requete(), _contexte())
        self.assertEqual(SessionPaiementEnLigne.objects.count(), 0)

    def test_montant_nul_rejete(self, mock_notif: MagicMock) -> None:
        mock_notif.return_value.valider_token.return_value = {"is_valid": True, "abonne_id": ABONNE}
        servicer = PaiementServicer()

        with self.assertRaises(ValidationError):
            servicer.CreerSessionPaiementEnLigne(self._requete(montant=0), _contexte())

    def test_facture_id_vide_rejete(self, mock_notif: MagicMock) -> None:
        mock_notif.return_value.valider_token.return_value = {"is_valid": True, "abonne_id": ABONNE}
        servicer = PaiementServicer()

        with self.assertRaises(ValidationError):
            servicer.CreerSessionPaiementEnLigne(self._requete(facture_id=""), _contexte())


@patch("paiements.grpc_server.publish_paiement_event")
@patch("paiements.grpc_server.publish_reporting_event")
@patch("paiements.services.NotificationServiceClient")
@patch("paiements.services.AbonneServiceClient")
@patch("paiements.grpc_server.NotificationServiceClient")
@patch("paiements.grpc_server.FacturationServiceClient")
class TestConfirmerSessionPaiementEnLigneRPC(TestCase):
    """Tests du RPC `ConfirmerSessionPaiementEnLigne`."""

    def _requete(self, session_id: str, token_espace: str) -> pb.ConfirmerSessionPaiementRequest:
        return pb.ConfirmerSessionPaiementRequest(session_id=session_id, token_espace=token_espace)

    def test_confirmation_reussie_encaisse_et_marque_confirmee(
        self,
        mock_fact: MagicMock,
        mock_notif: MagicMock,
        mock_abonne: MagicMock,
        mock_notif_svc: MagicMock,
        mock_rep: MagicMock,
        mock_pub: MagicMock,
    ) -> None:
        SoldeFactureRepository().create(
            facture_id=FACTURE,
            abonne_id=ABONNE,
            montant_total=Decimal("5000"),
            date_limite_paiement=date.today(),
        )
        session = _session(montant=5000, token_espace="token-1")
        servicer = PaiementServicer()

        reponse = servicer.ConfirmerSessionPaiementEnLigne(self._requete(str(session.id), "token-1"), _contexte())

        self.assertEqual(reponse.statut, StatutSessionPaiement.CONFIRMEE)
        session.refresh_from_db()
        self.assertEqual(session.statut, StatutSessionPaiement.CONFIRMEE)

        paiement = Paiement.objects.get(reference_transaction=str(session.id))
        self.assertEqual(paiement.mode_paiement, ModePaiement.MOBILE_MONEY)
        self.assertEqual(paiement.enregistre_par, SYSTEME_PAIEMENT_EN_LIGNE)
        self.assertEqual(paiement.abonne_id, ABONNE)
        mock_notif.return_value.envoyer_recu.assert_called_once()

    def test_token_different_rejete_sans_encaisser(
        self,
        mock_fact: MagicMock,
        mock_notif: MagicMock,
        mock_abonne: MagicMock,
        mock_notif_svc: MagicMock,
        mock_rep: MagicMock,
        mock_pub: MagicMock,
    ) -> None:
        """Anti-IDOR : un token par ailleurs valide mais différent ne doit
        jamais pouvoir confirmer la session d'un autre."""
        session = _session(token_espace="token-du-vrai-proprietaire")
        servicer = PaiementServicer()

        with self.assertRaises(ObjectDoesNotExist):
            servicer.ConfirmerSessionPaiementEnLigne(
                self._requete(str(session.id), "token-d-un-autre-abonne"), _contexte()
            )

        session.refresh_from_db()
        self.assertEqual(session.statut, StatutSessionPaiement.EN_ATTENTE)
        self.assertEqual(Paiement.objects.count(), 0)
        mock_notif.return_value.envoyer_recu.assert_not_called()

    def test_session_introuvable_rejetee(
        self,
        mock_fact: MagicMock,
        mock_notif: MagicMock,
        mock_abonne: MagicMock,
        mock_notif_svc: MagicMock,
        mock_rep: MagicMock,
        mock_pub: MagicMock,
    ) -> None:
        servicer = PaiementServicer()

        with self.assertRaises(ObjectDoesNotExist):
            servicer.ConfirmerSessionPaiementEnLigne(self._requete(str(uuid.uuid4()), "peu-importe"), _contexte())

    def test_confirmation_deja_confirmee_idempotente(
        self,
        mock_fact: MagicMock,
        mock_notif: MagicMock,
        mock_abonne: MagicMock,
        mock_notif_svc: MagicMock,
        mock_rep: MagicMock,
        mock_pub: MagicMock,
    ) -> None:
        """Un rejeu de la confirmation ne doit ni ré-encaisser, ni renvoyer
        d'erreur brute de contrainte d'unicité — juste l'état déjà tranché."""
        SoldeFactureRepository().create(
            facture_id=FACTURE,
            abonne_id=ABONNE,
            montant_total=Decimal("5000"),
            date_limite_paiement=date.today(),
        )
        session = _session(montant=5000, token_espace="token-1")
        servicer = PaiementServicer()
        premiere = servicer.ConfirmerSessionPaiementEnLigne(self._requete(str(session.id), "token-1"), _contexte())
        self.assertEqual(premiere.statut, StatutSessionPaiement.CONFIRMEE)
        mock_notif.return_value.envoyer_recu.reset_mock()

        rejouee = servicer.ConfirmerSessionPaiementEnLigne(self._requete(str(session.id), "token-1"), _contexte())

        self.assertEqual(rejouee.statut, StatutSessionPaiement.CONFIRMEE)
        self.assertEqual(Paiement.objects.filter(reference_transaction=str(session.id)).count(), 1)
        mock_notif.return_value.envoyer_recu.assert_not_called()

    def test_session_expiree_marquee_et_non_encaissee(
        self,
        mock_fact: MagicMock,
        mock_notif: MagicMock,
        mock_abonne: MagicMock,
        mock_notif_svc: MagicMock,
        mock_rep: MagicMock,
        mock_pub: MagicMock,
    ) -> None:
        session = _session(montant=5000, token_espace="token-1", expire_dans_minutes=-1)
        servicer = PaiementServicer()

        reponse = servicer.ConfirmerSessionPaiementEnLigne(self._requete(str(session.id), "token-1"), _contexte())

        self.assertEqual(reponse.statut, StatutSessionPaiement.EXPIREE)
        session.refresh_from_db()
        self.assertEqual(session.statut, StatutSessionPaiement.EXPIREE)
        self.assertEqual(Paiement.objects.count(), 0)
        mock_notif.return_value.envoyer_recu.assert_not_called()

    def test_passerelle_en_echec_marque_echouee_et_n_encaisse_pas(
        self,
        mock_fact: MagicMock,
        mock_notif: MagicMock,
        mock_abonne: MagicMock,
        mock_notif_svc: MagicMock,
        mock_rep: MagicMock,
        mock_pub: MagicMock,
    ) -> None:
        """Point d'extension : si `confirmer()` rendait un jour `False` (vrai
        fournisseur), aucun encaissement ne doit avoir lieu."""
        session = _session(montant=5000, token_espace="token-1")
        servicer = PaiementServicer()

        with patch("paiements.grpc_server.MockPasserellePaiementClient") as mock_passerelle_cls:
            mock_passerelle_cls.return_value.confirmer.return_value = False
            reponse = servicer.ConfirmerSessionPaiementEnLigne(self._requete(str(session.id), "token-1"), _contexte())

        self.assertEqual(reponse.statut, StatutSessionPaiement.ECHOUEE)
        session.refresh_from_db()
        self.assertEqual(session.statut, StatutSessionPaiement.ECHOUEE)
        self.assertEqual(Paiement.objects.count(), 0)
