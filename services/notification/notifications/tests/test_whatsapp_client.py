from unittest.mock import Mock, patch

import requests
from django.test import SimpleTestCase, override_settings

from notifications.whatsapp_client import WhatsAppDeliveryError, WhatsAppWebClient


class WhatsAppWebClientTests(SimpleTestCase):
    def setUp(self) -> None:
        self.wa_client = WhatsAppWebClient()

    @patch("notifications.whatsapp_client.requests.post")
    def test_send_success(self, mock_post: Mock) -> None:
        mock_post.return_value = Mock(status_code=200, json=Mock(return_value={"success": True}))
        self.wa_client.send("+237690000000", "Bonjour")
        mock_post.assert_called_once()

    @patch("notifications.whatsapp_client.requests.post")
    def test_send_network_error_raises_delivery_error(self, mock_post: Mock) -> None:
        mock_post.side_effect = requests.ConnectionError("refused")
        with self.assertRaises(WhatsAppDeliveryError):
            self.wa_client.send("+237690000000", "Bonjour")

    @patch("notifications.whatsapp_client.requests.post")
    def test_send_503_raises_before_parsing_body(self, mock_post: Mock) -> None:
        """Le service non connecté (503) doit lever WhatsAppDeliveryError
        sans jamais tenter de parser le corps de la réponse."""
        mock_post.return_value = Mock(status_code=503, json=Mock(side_effect=ValueError("not json")))
        with self.assertRaises(WhatsAppDeliveryError):
            self.wa_client.send("+237690000000", "Bonjour")

    @patch("notifications.whatsapp_client.requests.post")
    def test_send_non_json_body_raises_delivery_error(self, mock_post: Mock) -> None:
        """Régression ANO-024 : un corps de réponse non-JSON sur un statut
        HTTP inattendu doit lever WhatsAppDeliveryError, pas laisser fuiter
        une ValueError brute qui romprait la dégradation gracieuse."""
        mock_post.return_value = Mock(status_code=500, json=Mock(side_effect=ValueError("not json")))
        with self.assertRaises(WhatsAppDeliveryError):
            self.wa_client.send("+237690000000", "Bonjour")

    @patch("notifications.whatsapp_client.requests.post")
    def test_send_with_pdf_success(self, mock_post: Mock) -> None:
        mock_post.return_value = Mock(status_code=200, json=Mock(return_value={"success": True}))
        self.wa_client.send_with_pdf("+237690000000", "Bonjour", b"%PDF-1.4", "facture.pdf")
        mock_post.assert_called_once()

    @patch("notifications.whatsapp_client.requests.post")
    def test_send_with_pdf_non_json_body_raises_delivery_error(self, mock_post: Mock) -> None:
        """Régression ANO-024, variante send_with_pdf."""
        mock_post.return_value = Mock(status_code=500, json=Mock(side_effect=ValueError("not json")))
        with self.assertRaises(WhatsAppDeliveryError):
            self.wa_client.send_with_pdf("+237690000000", "Bonjour", b"%PDF-1.4", "facture.pdf")

    @patch("notifications.whatsapp_client.requests.get")
    def test_get_qr_non_connecte_retourne_qr(self, mock_get: Mock) -> None:
        mock_get.return_value = Mock(
            status_code=200, json=Mock(return_value={"ready": False, "qr": "data:image/png;base64,AAA", "number": ""})
        )
        ready, qr, number, phase, depuis = self.wa_client.get_qr()
        self.assertFalse(ready)
        self.assertEqual(qr, "data:image/png;base64,AAA")
        self.assertEqual(number, "")
        # Sans phase dans la réponse, on retombe sur « demarrage » plutôt que
        # sur une chaîne vide : l'UI teste la valeur, pas sa présence.
        self.assertEqual(phase, "demarrage")
        self.assertEqual(depuis, 0)

    @patch("notifications.whatsapp_client.requests.get")
    def test_get_qr_connecte_expose_le_numero(self, mock_get: Mock) -> None:
        mock_get.return_value = Mock(
            status_code=200, json=Mock(return_value={"ready": True, "qr": "", "number": "237675799743"})
        )
        ready, qr, number, _phase, _depuis = self.wa_client.get_qr()
        self.assertTrue(ready)
        self.assertEqual(qr, "")
        self.assertEqual(number, "237675799743")

    @patch("notifications.whatsapp_client.requests.get")
    def test_get_qr_transporte_la_phase_et_l_anciennete(self, mock_get: Mock) -> None:
        """« demarrage » et « rupture » appellent des messages opposés.

        L'écran ne recevait qu'un booléen à faux, qui recouvrait les deux :
        « le service démarre, patientez » et « la liaison est tombée, il faut
        rescanner ». Il affichait donc la même attente sans fin dans les deux
        cas — d'où l'impression qu'il faut recharger pour voir le QR.
        """
        mock_get.return_value = Mock(
            status_code=200,
            json=Mock(return_value={"ready": False, "qr": "", "number": "", "phase": "rupture", "depuis": 420000}),
        )
        ready, _qr, _number, phase, depuis = self.wa_client.get_qr()
        self.assertFalse(ready)
        self.assertEqual(phase, "rupture")
        self.assertEqual(depuis, 420000)

    @patch("notifications.whatsapp_client.requests.get")
    def test_get_qr_depuis_nul_ne_devient_pas_none(self, mock_get: Mock) -> None:
        """`depuis` vaut null tant qu'on n'a jamais été connecté — 0 côté proto."""
        mock_get.return_value = Mock(
            status_code=200,
            json=Mock(return_value={"ready": False, "qr": "", "number": "", "phase": "demarrage", "depuis": None}),
        )
        *_rest, depuis = self.wa_client.get_qr()
        self.assertEqual(depuis, 0)

    @patch("notifications.whatsapp_client.requests.get")
    def test_get_qr_network_error_raises_delivery_error(self, mock_get: Mock) -> None:
        mock_get.side_effect = requests.ConnectionError("refused")
        with self.assertRaises(WhatsAppDeliveryError):
            self.wa_client.get_qr()

    @patch("notifications.whatsapp_client.requests.get")
    def test_get_qr_non_json_body_raises_delivery_error(self, mock_get: Mock) -> None:
        mock_get.return_value = Mock(status_code=502, json=Mock(side_effect=ValueError("not json")))
        with self.assertRaises(WhatsAppDeliveryError):
            self.wa_client.get_qr()

    # ── WHATSAPP_DISABLE_SEND_FOR_TESTS ──────────────────────────────────────
    #
    # Garde-fou de test : voir la docstring de notifications/whatsapp_client.py.
    # Réservé aux environnements de test — jamais activé en production.

    @override_settings(WHATSAPP_DISABLE_SEND_FOR_TESTS=True)
    @patch("notifications.whatsapp_client.throttle_whatsapp_send")
    @patch("notifications.whatsapp_client.requests.post")
    def test_send_desactive_ne_contacte_pas_le_service_reel(self, mock_post: Mock, mock_throttle: Mock) -> None:
        """Activé : aucun appel réseau, aucun throttle, succès simulé."""
        self.wa_client.send("+237690000000", "Bonjour")
        mock_post.assert_not_called()
        mock_throttle.assert_not_called()

    @override_settings(WHATSAPP_DISABLE_SEND_FOR_TESTS=True)
    def test_send_desactive_journalise_le_message_explicite(self) -> None:
        with self.assertLogs("notifications.whatsapp_client", level="WARNING") as logs:
            self.wa_client.send("+237690000000", "Bonjour")
        self.assertTrue(
            any(
                "[TEST] envoi WhatsApp simulé, désactivé par WHATSAPP_DISABLE_SEND_FOR_TESTS" in message
                for message in logs.output
            )
        )

    @override_settings(WHATSAPP_DISABLE_SEND_FOR_TESTS=True)
    @patch("notifications.whatsapp_client.throttle_whatsapp_send")
    @patch("notifications.whatsapp_client.requests.post")
    def test_send_with_pdf_desactive_ne_contacte_pas_le_service_reel(
        self, mock_post: Mock, mock_throttle: Mock
    ) -> None:
        self.wa_client.send_with_pdf("+237690000000", "Bonjour", b"%PDF-1.4", "recu.pdf")
        mock_post.assert_not_called()
        mock_throttle.assert_not_called()

    @override_settings(WHATSAPP_DISABLE_SEND_FOR_TESTS=False)
    @patch("notifications.whatsapp_client.requests.post")
    def test_send_absente_tente_toujours_l_appel_reel(self, mock_post: Mock) -> None:
        """Désactivée (valeur par défaut) : comportement inchangé, appel réel tenté."""
        mock_post.return_value = Mock(status_code=200, json=Mock(return_value={"success": True}))
        self.wa_client.send("+237690000000", "Bonjour")
        mock_post.assert_called_once()

    @override_settings(WHATSAPP_DISABLE_SEND_FOR_TESTS=False)
    @patch("notifications.whatsapp_client.requests.post")
    def test_send_with_pdf_absente_tente_toujours_l_appel_reel(self, mock_post: Mock) -> None:
        mock_post.return_value = Mock(status_code=200, json=Mock(return_value={"success": True}))
        self.wa_client.send_with_pdf("+237690000000", "Bonjour", b"%PDF-1.4", "recu.pdf")
        mock_post.assert_called_once()
