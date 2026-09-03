from unittest.mock import Mock, patch

from django.conf import settings
from django.test import SimpleTestCase

from comptes.email_client import BrevoEmailClient, EmailDeliveryError


class BrevoEmailClientTests(SimpleTestCase):
    def setUp(self) -> None:
        self.brevo_client = BrevoEmailClient()

    @patch("comptes.email_client.requests.post")
    def test_send_success_calls_brevo_api(self, mock_post: Mock) -> None:
        mock_post.return_value = Mock(status_code=201)

        self.brevo_client.send(
            to_email="user@example.com", to_name="User", subject="Sujet", html_content="<p>Contenu</p>"
        )

        mock_post.assert_called_once()
        _, kwargs = mock_post.call_args
        self.assertEqual(kwargs["headers"]["api-key"], settings.BREVO_API_KEY)
        self.assertEqual(kwargs["json"]["to"], [{"email": "user@example.com", "name": "User"}])
        self.assertEqual(kwargs["json"]["subject"], "Sujet")

    @patch("comptes.email_client.requests.post")
    def test_send_failure_raises_email_delivery_error(self, mock_post: Mock) -> None:
        mock_post.return_value = Mock(status_code=400, text="Bad Request")

        with self.assertRaises(EmailDeliveryError):
            self.brevo_client.send(to_email="user@example.com", to_name="User", subject="Sujet", html_content="<p/>")
