"""Tests des resolvers GraphQL du Notification Service (gateway).

Régression ANO-022 : aucun test n'existait pour ce domaine.
"""

from unittest.mock import MagicMock, patch

from django.test import SimpleTestCase

from schema.notification_mutations import NotificationMutations
from schema.notification_queries import NotificationQueries


def _envoi_response(**kwargs) -> MagicMock:
    defaults = dict(
        envoi_id="envoi-001",
        abonne_id="abonne-001",
        facture_id="facture-001",
        type_envoi="FACTURE",
        statut="ENVOYE",
        date_envoi="2026-07-02T10:00:00",
        telnyx_message_id="msg-123",
        erreur="",
    )
    defaults.update(kwargs)
    return MagicMock(**defaults)


class TestEnvoiMapping(SimpleTestCase):
    @patch("schema.notification_queries.notification_client")
    @patch("schema.notification_queries.require_auth")
    @patch("schema.notification_queries.require_role")
    def test_envoi_expose_type_abonne_message_raison(self, mock_role, mock_auth, mock_client) -> None:
        mock_auth.return_value = MagicMock(role="ADMIN")
        mock_client.get_envoi.return_value = _envoi_response(
            type_envoi="RELANCE_2", erreur="numéro invalide", statut="ECHEC"
        )
        result = NotificationQueries().envoi(MagicMock(), envoi_id="envoi-001")
        self.assertEqual(result.type_envoi, "RELANCE_2")
        self.assertEqual(result.abonne_id, "abonne-001")
        self.assertEqual(result.message_id, "msg-123")
        self.assertEqual(result.raison_echec, "numéro invalide")


class TestRenvoyerEnvoi(SimpleTestCase):
    @patch("schema.notification_mutations.notification_client")
    @patch("schema.notification_mutations.require_auth")
    @patch("schema.notification_mutations.require_role")
    def test_renvoyer_envoi_resout_facture_et_renvoie(self, mock_role, mock_auth, mock_client) -> None:
        mock_auth.return_value = MagicMock(role="COMPTABLE")
        mock_client.get_envoi.return_value = _envoi_response(facture_id="facture-9")
        mock_client.renvoyer_facture.return_value = _envoi_response(envoi_id="envoi-2", facture_id="facture-9")
        result = NotificationMutations().renvoyer_envoi(MagicMock(), envoi_id="envoi-001")
        mock_client.get_envoi.assert_called_once_with("envoi-001")
        mock_client.renvoyer_facture.assert_called_once_with(facture_id="facture-9")
        self.assertEqual(result.envoi_id, "envoi-2")


class TestNotificationQueries(SimpleTestCase):
    @patch("schema.notification_queries.notification_client")
    @patch("schema.notification_queries.require_auth")
    @patch("schema.notification_queries.require_role")
    def test_envoi_par_id(self, mock_role, mock_auth, mock_client) -> None:
        mock_auth.return_value = MagicMock(role="COMPTABLE")
        mock_client.get_envoi.return_value = _envoi_response()
        info = MagicMock()
        result = NotificationQueries().envoi(info, envoi_id="envoi-001")
        self.assertEqual(result.envoi_id, "envoi-001")
        self.assertEqual(result.statut, "ENVOYE")

    @patch("schema.notification_queries.notification_client")
    @patch("schema.notification_queries.require_auth")
    @patch("schema.notification_queries.require_role")
    def test_envois_avec_filtres(self, mock_role, mock_auth, mock_client) -> None:
        mock_auth.return_value = MagicMock(role="ADMIN")
        mock_client.list_envois.return_value = MagicMock(
            envois=[_envoi_response(), _envoi_response(envoi_id="envoi-002")]
        )
        info = MagicMock()
        result = NotificationQueries().envois(info, facture_id="facture-001")
        self.assertEqual(len(result), 2)
        mock_client.list_envois.assert_called_once_with(facture_id="facture-001", abonne_id="")

    @patch("schema.notification_queries.notification_client")
    @patch("schema.notification_queries.require_auth")
    @patch("schema.notification_queries.require_role")
    def test_whatsapp_qr_admin(self, mock_role, mock_auth, mock_client) -> None:
        mock_auth.return_value = MagicMock(role="ADMIN")
        mock_client.get_whatsapp_qr.return_value = MagicMock(ready=False, qr="data:image/png;base64,AAA", number="")
        info = MagicMock()
        result = NotificationQueries().whatsapp_qr(info)
        self.assertFalse(result.ready)
        self.assertEqual(result.qr, "data:image/png;base64,AAA")
        mock_role.assert_called_once_with(info, "ADMIN")

    @patch("schema.notification_queries.notification_client")
    @patch("schema.notification_queries.require_auth")
    @patch("schema.notification_queries.require_role")
    def test_whatsapp_qr_connecte_expose_le_numero(self, mock_role, mock_auth, mock_client) -> None:
        mock_auth.return_value = MagicMock(role="ADMIN")
        mock_client.get_whatsapp_qr.return_value = MagicMock(ready=True, qr="", number="237675799743")
        info = MagicMock()
        result = NotificationQueries().whatsapp_qr(info)
        self.assertTrue(result.ready)
        self.assertEqual(result.number, "237675799743")


class TestNotificationMutations(SimpleTestCase):
    @patch("schema.notification_mutations.notification_client")
    @patch("schema.notification_mutations.require_auth")
    @patch("schema.notification_mutations.require_role")
    def test_envoyer_facture_whatsapp(self, mock_role, mock_auth, mock_client) -> None:
        mock_auth.return_value = MagicMock(role="COMPTABLE")
        mock_client.envoyer_facture.return_value = _envoi_response()
        info = MagicMock()
        result = NotificationMutations().envoyer_facture_whatsapp(
            info, facture_id="facture-001", abonne_id="abonne-001"
        )
        self.assertEqual(result.statut, "ENVOYE")
        mock_client.envoyer_facture.assert_called_once_with(facture_id="facture-001", abonne_id="abonne-001")

    @patch("schema.notification_mutations.notification_client")
    @patch("schema.notification_mutations.require_auth")
    @patch("schema.notification_mutations.require_role")
    def test_renvoyer_facture_whatsapp(self, mock_role, mock_auth, mock_client) -> None:
        mock_auth.return_value = MagicMock(role="ADMIN")
        mock_client.renvoyer_facture.return_value = _envoi_response(envoi_id="envoi-003")
        info = MagicMock()
        result = NotificationMutations().renvoyer_facture_whatsapp(info, facture_id="facture-001")
        self.assertEqual(result.envoi_id, "envoi-003")

    @patch("schema.notification_mutations.notification_client")
    @patch("schema.notification_mutations.require_auth")
    @patch("schema.notification_mutations.require_role")
    def test_revoquer_token_abonne(self, mock_role, mock_auth, mock_client) -> None:
        mock_auth.return_value = MagicMock(role="ADMIN")
        mock_client.revoquer_token.return_value = MagicMock(success=True)
        info = MagicMock()
        result = NotificationMutations().revoquer_token_abonne(info, token_id="token-001")
        self.assertTrue(result)
        mock_role.assert_called_once_with(info, "ADMIN")

    @patch("schema.notification_mutations.notification_client")
    @patch("schema.notification_mutations.require_auth")
    @patch("schema.notification_mutations.require_role")
    def test_revoquer_tous_tokens_abonnes(self, mock_role, mock_auth, mock_client) -> None:
        mock_auth.return_value = MagicMock(role="ADMIN")
        mock_client.revoquer_tous_tokens.return_value = MagicMock(count=3)
        info = MagicMock()
        result = NotificationMutations().revoquer_tous_tokens_abonnes(info)
        self.assertEqual(result, 3)
        mock_role.assert_called_once_with(info, "ADMIN")

    @patch("schema.notification_mutations.notification_client")
    @patch("schema.notification_mutations.require_auth")
    @patch("schema.notification_mutations.require_role")
    def test_tester_envoi_whatsapp_succes(self, mock_role, mock_auth, mock_client) -> None:
        mock_auth.return_value = MagicMock(role="ADMIN")
        mock_client.tester_envoi.return_value = MagicMock(success=True, message="Message de test envoyé")
        info = MagicMock()
        result = NotificationMutations().tester_envoi_whatsapp(info, phone_number="+237699000001")
        self.assertTrue(result.success)
        mock_client.tester_envoi.assert_called_once_with(phone_number="+237699000001")

    @patch("schema.notification_mutations.notification_client")
    @patch("schema.notification_mutations.require_auth")
    @patch("schema.notification_mutations.require_role")
    def test_tester_envoi_whatsapp_echec_remonte_le_motif(self, mock_role, mock_auth, mock_client) -> None:
        mock_auth.return_value = MagicMock(role="ADMIN")
        mock_client.tester_envoi.return_value = MagicMock(
            success=False, message="WhatsApp non connecté — scannez le QR code sur /qr"
        )
        info = MagicMock()
        result = NotificationMutations().tester_envoi_whatsapp(info, phone_number="+237699000001")
        self.assertFalse(result.success)
        self.assertIn("non connecté", result.message)
