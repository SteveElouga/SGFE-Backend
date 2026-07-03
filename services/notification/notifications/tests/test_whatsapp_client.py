from unittest.mock import Mock, patch

import requests
from django.test import SimpleTestCase

from notifications.whatsapp_client import WhatsAppDeliveryError, WhatsAppWebClient


class WhatsAppWebClientTests(SimpleTestCase):
    def setUp(self):
        self.client = WhatsAppWebClient()

    @patch("notifications.whatsapp_client.requests.post")
    def test_send_success(self, mock_post):
        mock_post.return_value = Mock(status_code=200, json=Mock(return_value={"success": True}))
        self.client.send("+237690000000", "Bonjour")
        mock_post.assert_called_once()

    @patch("notifications.whatsapp_client.requests.post")
    def test_send_network_error_raises_delivery_error(self, mock_post):
        mock_post.side_effect = requests.ConnectionError("refused")
        with self.assertRaises(WhatsAppDeliveryError):
            self.client.send("+237690000000", "Bonjour")

    @patch("notifications.whatsapp_client.requests.post")
    def test_send_503_raises_before_parsing_body(self, mock_post):
        """Le service non connecté (503) doit lever WhatsAppDeliveryError
        sans jamais tenter de parser le corps de la réponse."""
        mock_post.return_value = Mock(status_code=503, json=Mock(side_effect=ValueError("not json")))
        with self.assertRaises(WhatsAppDeliveryError):
            self.client.send("+237690000000", "Bonjour")

    @patch("notifications.whatsapp_client.requests.post")
    def test_send_non_json_body_raises_delivery_error(self, mock_post):
        """Régression ANO-024 : un corps de réponse non-JSON sur un statut
        HTTP inattendu doit lever WhatsAppDeliveryError, pas laisser fuiter
        une ValueError brute qui romprait la dégradation gracieuse."""
        mock_post.return_value = Mock(status_code=500, json=Mock(side_effect=ValueError("not json")))
        with self.assertRaises(WhatsAppDeliveryError):
            self.client.send("+237690000000", "Bonjour")

    @patch("notifications.whatsapp_client.requests.post")
    def test_send_with_pdf_success(self, mock_post):
        mock_post.return_value = Mock(status_code=200, json=Mock(return_value={"success": True}))
        self.client.send_with_pdf("+237690000000", "Bonjour", b"%PDF-1.4", "facture.pdf")
        mock_post.assert_called_once()

    @patch("notifications.whatsapp_client.requests.post")
    def test_send_with_pdf_non_json_body_raises_delivery_error(self, mock_post):
        """Régression ANO-024, variante send_with_pdf."""
        mock_post.return_value = Mock(status_code=500, json=Mock(side_effect=ValueError("not json")))
        with self.assertRaises(WhatsAppDeliveryError):
            self.client.send_with_pdf("+237690000000", "Bonjour", b"%PDF-1.4", "facture.pdf")
