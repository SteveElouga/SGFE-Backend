import requests
from django.conf import settings

BREVO_API_URL = "https://api.brevo.com/v3/smtp/email"


class EmailDeliveryError(Exception):
    """L'envoi de l'e-mail a échoué côté fournisseur (Brevo)."""


class BrevoEmailClient:
    """Client pour l'API transactionnelle Brevo (300 e-mails/jour gratuits).

    Doc : https://developers.brevo.com/docs/send-a-transactional-email
    """

    def send(self, to_email: str, to_name: str, subject: str, html_content: str) -> None:
        response = requests.post(
            BREVO_API_URL,
            headers={
                "api-key": settings.BREVO_API_KEY,
                "content-type": "application/json",
                "accept": "application/json",
            },
            json={
                "sender": {"name": settings.BREVO_SENDER_NAME, "email": settings.BREVO_SENDER_EMAIL},
                "to": [{"email": to_email, "name": to_name}],
                "subject": subject,
                "htmlContent": html_content,
            },
            timeout=10,
        )
        if response.status_code >= 400:
            raise EmailDeliveryError(f"Brevo a renvoyé {response.status_code}: {response.text}")


email_client = BrevoEmailClient()
