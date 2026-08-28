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

        mock_fact.get_facture.return_value = _make_facture_mock(facture_id=facture_id, abonne_id=abonne_id)
        mock_fact.get_facture_pdf.return_value = (b"", "")
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
    def test_envoyer_facture_whatsapp_ko_degradation_gracieuse(self, mock_fact, mock_abonne, mock_config, mock_wa):
        """Si WhatsApp échoue, EnvoyerFacture retourne ECHEC sans abort gRPC."""
        facture_id = str(uuid.uuid4())
        abonne_id = str(uuid.uuid4())

        mock_fact.get_facture.return_value = _make_facture_mock(facture_id=facture_id, abonne_id=abonne_id)
        mock_fact.get_facture_pdf.return_value = (b"", "")
        mock_abonne.get_abonne.return_value = _make_abonne_mock(abonne_id=abonne_id)
        mock_config.get_token_validite_jours.return_value = 20
        mock_wa.send.side_effect = WhatsAppDeliveryError("Service WhatsApp inaccessible")

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
    def test_envoyer_relance_etape_valide(self, mock_fact, mock_abonne, mock_config, mock_wa):
        """EnvoyerRelance avec étape valide retourne un EnvoiResponse ENVOYE."""
        facture_id = str(uuid.uuid4())
        abonne_id = str(uuid.uuid4())

        mock_fact.get_facture.return_value = _make_facture_mock(facture_id=facture_id, abonne_id=abonne_id)
        mock_abonne.get_abonne.return_value = _make_abonne_mock(abonne_id=abonne_id)
        mock_config.get_token_validite_jours.return_value = 20
        mock_wa.send.return_value = None

        servicer = NotificationServiceServicer()
        request = pb.EnvoyerRelanceRequest(facture_id=facture_id, abonne_id=abonne_id, etape=2)
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


class TestGetEspaceUrlRPC(TestCase):
    """Tests du RPC GetEspaceUrl."""

    @patch("notifications.services.config_client")
    def test_get_espace_url_cree_token_et_retourne_url(self, mock_config):
        """GetEspaceUrl crée un token si besoin et renvoie l'URL + expiration ISO."""
        mock_config.get_token_validite_jours.return_value = 20
        servicer = NotificationServiceServicer()
        request = pb.GetEspaceUrlRequest(abonne_id="abo-1", facture_id="fac-1")
        context = MagicMock()

        response = servicer.GetEspaceUrl(request, context)

        token = TokenAcces.objects.get(abonne_id="abo-1")
        self.assertIn("/espace/", response.url)
        self.assertIn(str(token.token), response.url)
        self.assertEqual(response.date_expiration, token.date_expiration.isoformat())

    def test_get_espace_url_reutilise_token_existant(self):
        """Un token valide existant de l'abonné est réutilisé (pas de doublon)."""
        existant = TokenAcces.objects.create(
            abonne_id="abo-2",
            facture_id="fac-old",
            date_expiration=date.today() + timedelta(days=10),
        )
        servicer = NotificationServiceServicer()
        request = pb.GetEspaceUrlRequest(abonne_id="abo-2", facture_id="fac-new")
        context = MagicMock()

        response = servicer.GetEspaceUrl(request, context)

        self.assertIn(str(existant.token), response.url)
        self.assertEqual(TokenAcces.objects.filter(abonne_id="abo-2").count(), 1)


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


class TestRevoquerTousTokensRPC(TestCase):
    """Tests du RPC RevoquerTousTokens (révocation de masse)."""

    def test_revoque_les_actifs_et_retourne_le_compte(self):
        for _ in range(2):
            TokenAcces.objects.create(
                abonne_id=str(uuid.uuid4()),
                facture_id=str(uuid.uuid4()),
                date_expiration=date.today() + timedelta(days=20),
            )
        deja_revoque = TokenAcces.objects.create(
            abonne_id=str(uuid.uuid4()),
            facture_id=str(uuid.uuid4()),
            date_expiration=date.today() + timedelta(days=20),
            is_active=False,
        )

        servicer = NotificationServiceServicer()
        response = servicer.RevoquerTousTokens(pb.EmptyRequest(), MagicMock())

        self.assertEqual(response.count, 2)
        self.assertEqual(TokenAcces.objects.filter(is_active=True).count(), 0)
        deja_revoque.refresh_from_db()
        self.assertFalse(deja_revoque.is_active)


class TestGetWhatsAppQrRPC(TestCase):
    """Tests du RPC GetWhatsAppQr (numéro appairé inclus)."""

    @patch("notifications.services.whatsapp_client")
    def test_connecte_expose_le_numero(self, mock_wa):
        mock_wa.get_qr.return_value = (True, "", "237675799743", "connecte", 0)

        servicer = NotificationServiceServicer()
        response = servicer.GetWhatsAppQr(pb.EmptyRequest(), MagicMock())

        self.assertTrue(response.ready)
        self.assertEqual(response.number, "237675799743")
        self.assertEqual(response.phase, "connecte")

    @patch("notifications.services.whatsapp_client")
    def test_la_phase_survit_au_passage_en_protobuf(self, mock_wa):
        """C'est la frontière où l'information se perdait.

        Le message ne portait que ready/qr/number : tout ce qui expliquait
        *pourquoi* la liaison n'était pas prête s'arrêtait au service Django.
        """
        mock_wa.get_qr.return_value = (False, "", "", "rupture", 420000)

        servicer = NotificationServiceServicer()
        response = servicer.GetWhatsAppQr(pb.EmptyRequest(), MagicMock())

        self.assertFalse(response.ready)
        self.assertEqual(response.phase, "rupture")
        self.assertEqual(response.depuis_ms, 420000)


class TestTesterEnvoiRPC(TestCase):
    """Tests du RPC TesterEnvoi."""

    @patch("notifications.services.whatsapp_client")
    def test_tester_envoi_succes(self, mock_wa):
        mock_wa.send.return_value = None

        servicer = NotificationServiceServicer()
        response = servicer.TesterEnvoi(pb.TesterEnvoiRequest(phone_number="+237699000001"), MagicMock())

        self.assertTrue(response.success)
        mock_wa.send.assert_called_once()

    @patch("notifications.services.whatsapp_client")
    def test_tester_envoi_echec_retourne_le_motif_reel(self, mock_wa):
        """En cas d'échec, le motif exact est renvoyé (pas d'abort ni de message générique)."""
        mock_wa.send.side_effect = WhatsAppDeliveryError(
            "WhatsApp n'est pas connecté. Un administrateur doit lier le compte "
            "depuis Configuration › WhatsApp & Tokens."
        )

        servicer = NotificationServiceServicer()
        context = MagicMock()
        response = servicer.TesterEnvoi(pb.TesterEnvoiRequest(phone_number="+237699000001"), context)

        self.assertFalse(response.success)
        self.assertIn("pas connecté", response.message)
        # Le motif remonté ne doit nommer aucune route interne.
        self.assertNotIn("/qr", response.message)
        context.abort.assert_not_called()

    def test_tester_envoi_numero_vide_leve_value_error(self):
        servicer = NotificationServiceServicer()
        with self.assertRaises(ValueError):
            servicer.TesterEnvoi(pb.TesterEnvoiRequest(phone_number=""), MagicMock())


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
