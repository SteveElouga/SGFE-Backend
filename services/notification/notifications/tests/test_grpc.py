"""Tests du serveur gRPC du Notification Service.

Vérifie le comportement des RPCs : codes de retour, dégradation gracieuse,
gestion des erreurs.
"""

import sys
import uuid
from datetime import date, timedelta
from pathlib import Path
from unittest.mock import MagicMock, patch

from django.conf import settings
from django.test import TestCase

# Ajout du chemin des stubs gRPC
_proto_path = str(Path(settings.BASE_DIR) / "proto")
if _proto_path not in sys.path:
    sys.path.insert(0, _proto_path)

import notification_service_pb2 as pb  # type: ignore[import]  # noqa: E402

from notifications.grpc_server import NotificationServiceServicer  # noqa: E402
from notifications.models import Envoi, StatutEnvoi, TokenAcces, TypeEnvoi  # noqa: E402
from notifications.whatsapp_client import WhatsAppDeliveryError  # noqa: E402


def _make_facture_mock(
    facture_id: str | None = None,
    abonne_id: str | None = None,
    consommation: float = 15.0,
    montant: float = 7500.0,
    date_releve: str = "2025-07-01",
    date_limite_paiement: str = "2025-07-20",
) -> MagicMock:
    mock = MagicMock()
    mock.facture_id = facture_id or str(uuid.uuid4())
    mock.abonne_id = abonne_id or str(uuid.uuid4())
    mock.consommation = consommation
    mock.montant = montant
    mock.date_releve = date_releve
    mock.date_limite_paiement = date_limite_paiement
    return mock


def _make_abonne_mock(
    abonne_id: str | None = None,
    nom: str = "DUPONT",
    prenom: str = "Jean",
    telephone: str = "+237699000001",
) -> MagicMock:
    mock = MagicMock()
    mock.abonne_id = abonne_id or str(uuid.uuid4())
    mock.nom = nom
    mock.prenom = prenom
    mock.telephone_whatsapp = telephone
    return mock


class TestEnvoyerFactureRPC(TestCase):
    """Tests du RPC EnvoyerFacture."""

    @patch("notifications.services.whatsapp_client")
    @patch("notifications.services.config_client")
    @patch("notifications.services.abonne_client")
    @patch("notifications.services.facturation_client")
    def test_envoyer_facture_succes(self, mock_fact, mock_abonne, mock_config, mock_wa):
        """EnvoyerFacture retourne un EnvoiResponse ENVOYE en cas de succès."""
        facture_id = str(uuid.uuid4())
        abonne_id = str(uuid.uuid4())

        mock_fact.get_facture.return_value = _make_facture_mock(
            facture_id=facture_id, abonne_id=abonne_id
        )
        mock_abonne.get_abonne.return_value = _make_abonne_mock(abonne_id=abonne_id)
        mock_config.get_token_validite_jours.return_value = 20
        mock_wa.send.return_value = None

        servicer = NotificationServiceServicer()
        request = pb.EnvoyerFactureRequest(facture_id=facture_id, abonne_id=abonne_id)
        context = MagicMock()

        response = servicer.EnvoyerFacture(request, context)

        self.assertIsInstance(response, pb.EnvoiResponse)
        self.assertEqual(response.statut, StatutEnvoi.ENVOYE)
        self.assertEqual(response.facture_id, facture_id)
        context.abort.assert_not_called()

    @patch("notifications.services.whatsapp_client")
    @patch("notifications.services.config_client")
    @patch("notifications.services.abonne_client")
    @patch("notifications.services.facturation_client")
    def test_envoyer_facture_whatsapp_ko_degradation_gracieuse(
        self, mock_fact, mock_abonne, mock_config, mock_wa
    ):
        """Si WhatsApp échoue, EnvoyerFacture retourne ECHEC sans abort gRPC."""
        facture_id = str(uuid.uuid4())
        abonne_id = str(uuid.uuid4())

        mock_fact.get_facture.return_value = _make_facture_mock(
            facture_id=facture_id, abonne_id=abonne_id
        )
        mock_abonne.get_abonne.return_value = _make_abonne_mock(abonne_id=abonne_id)
        mock_config.get_token_validite_jours.return_value = 20
        mock_wa.send.side_effect = WhatsAppDeliveryError(
            "Service WhatsApp inaccessible"
        )

        servicer = NotificationServiceServicer()
        request = pb.EnvoyerFactureRequest(facture_id=facture_id, abonne_id=abonne_id)
        context = MagicMock()

        response = servicer.EnvoyerFacture(request, context)

        self.assertEqual(response.statut, StatutEnvoi.ECHEC)
        self.assertIn("inaccessible", response.erreur)
        # Pas d'abort gRPC — dégradation gracieuse
        context.abort.assert_not_called()


class TestEnvoyerRelanceRPC(TestCase):
    """Tests du RPC EnvoyerRelance."""

    @patch("notifications.services.whatsapp_client")
    @patch("notifications.services.config_client")
    @patch("notifications.services.abonne_client")
    @patch("notifications.services.facturation_client")
    def test_envoyer_relance_etape_valide(
        self, mock_fact, mock_abonne, mock_config, mock_wa
    ):
        """EnvoyerRelance avec étape valide retourne un EnvoiResponse ENVOYE."""
        facture_id = str(uuid.uuid4())
        abonne_id = str(uuid.uuid4())

        mock_fact.get_facture.return_value = _make_facture_mock(
            facture_id=facture_id, abonne_id=abonne_id
        )
        mock_abonne.get_abonne.return_value = _make_abonne_mock(abonne_id=abonne_id)
        mock_config.get_token_validite_jours.return_value = 20
        mock_wa.send.return_value = None

        servicer = NotificationServiceServicer()
        request = pb.EnvoyerRelanceRequest(
            facture_id=facture_id, abonne_id=abonne_id, etape=2
        )
        context = MagicMock()

        response = servicer.EnvoyerRelance(request, context)

        self.assertIsInstance(response, pb.EnvoiResponse)
        self.assertEqual(response.statut, StatutEnvoi.ENVOYE)

    def test_envoyer_relance_etape_invalide_leve_erreur(self):
        """EnvoyerRelance avec étape invalide doit lever ValidationError (interceptée en INVALID_ARGUMENT)."""
        from django.core.exceptions import ValidationError

        servicer = NotificationServiceServicer()
        request = pb.EnvoyerRelanceRequest(facture_id="fid", abonne_id="aid", etape=99)
        context = MagicMock()

        with self.assertRaises(ValidationError):
            servicer.EnvoyerRelance(request, context)


class TestValiderTokenRPC(TestCase):
    """Tests du RPC ValiderToken."""

    def test_valider_token_valide(self):
        """ValiderToken retourne is_valid=True pour un token actif non expiré."""
        token = TokenAcces.objects.create(
            abonne_id=str(uuid.uuid4()),
            facture_id=str(uuid.uuid4()),
            date_expiration=date.today() + timedelta(days=20),
        )

        servicer = NotificationServiceServicer()
        request = pb.ValiderTokenRequest(token=str(token.token))
        context = MagicMock()

        response = servicer.ValiderToken(request, context)

        self.assertIsInstance(response, pb.ValiderTokenResponse)
        self.assertTrue(response.is_valid)
        self.assertEqual(response.abonne_id, token.abonne_id)

    def test_valider_token_expire_retourne_invalide(self):
        """ValiderToken retourne is_valid=False pour un token expiré."""
        token = TokenAcces.objects.create(
            abonne_id=str(uuid.uuid4()),
            facture_id=str(uuid.uuid4()),
            date_expiration=date.today() - timedelta(days=1),
        )

        servicer = NotificationServiceServicer()
        request = pb.ValiderTokenRequest(token=str(token.token))
        context = MagicMock()

        response = servicer.ValiderToken(request, context)

        self.assertFalse(response.is_valid)
        context.abort.assert_not_called()

    def test_valider_token_inconnu_retourne_invalide(self):
        """ValiderToken retourne is_valid=False pour un UUID inexistant."""
        servicer = NotificationServiceServicer()
        request = pb.ValiderTokenRequest(token=str(uuid.uuid4()))
        context = MagicMock()

        response = servicer.ValiderToken(request, context)

        self.assertFalse(response.is_valid)
        context.abort.assert_not_called()


class TestRevoquerTokenRPC(TestCase):
    """Tests du RPC RevoquerToken."""

    def test_revoquer_token_succes(self):
        """RevoquerToken retourne StatusResponse(success=True)."""
        token = TokenAcces.objects.create(
            abonne_id=str(uuid.uuid4()),
            facture_id=str(uuid.uuid4()),
            date_expiration=date.today() + timedelta(days=20),
        )

        servicer = NotificationServiceServicer()
        request = pb.TokenIdRequest(token_id=str(token.id))
        context = MagicMock()

        response = servicer.RevoquerToken(request, context)

        self.assertTrue(response.success)
        token.refresh_from_db()
        self.assertFalse(token.is_active)

    def test_revoquer_token_introuvable_leve_erreur(self):
        """RevoquerToken lève ObjectDoesNotExist si le token est introuvable."""
        from django.core.exceptions import ObjectDoesNotExist

        servicer = NotificationServiceServicer()
        request = pb.TokenIdRequest(token_id=str(uuid.uuid4()))
        context = MagicMock()

        with self.assertRaises(ObjectDoesNotExist):
            servicer.RevoquerToken(request, context)


class TestGetEnvoiRPC(TestCase):
    """Tests du RPC GetEnvoi."""

    def test_get_envoi_introuvable_leve_erreur(self):
        """GetEnvoi lève ObjectDoesNotExist si l'envoi est introuvable."""
        from django.core.exceptions import ObjectDoesNotExist

        servicer = NotificationServiceServicer()
        request = pb.EnvoiIdRequest(envoi_id=str(uuid.uuid4()))
        context = MagicMock()

        with self.assertRaises(ObjectDoesNotExist):
            servicer.GetEnvoi(request, context)

    def test_get_envoi_existant(self):
        """GetEnvoi retourne l'EnvoiResponse correspondant."""
        envoi = Envoi.objects.create(
            facture_id=str(uuid.uuid4()),
            abonne_id=str(uuid.uuid4()),
            type_envoi=TypeEnvoi.FACTURE,
            telephone="+237699000001",
            statut=StatutEnvoi.ENVOYE,
        )

        servicer = NotificationServiceServicer()
        request = pb.EnvoiIdRequest(envoi_id=str(envoi.id))
        context = MagicMock()

        response = servicer.GetEnvoi(request, context)

        self.assertIsInstance(response, pb.EnvoiResponse)
        self.assertEqual(response.envoi_id, str(envoi.id))
        self.assertEqual(response.statut, StatutEnvoi.ENVOYE)
