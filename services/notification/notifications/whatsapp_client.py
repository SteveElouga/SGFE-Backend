"""Client HTTP vers le service whatsapp-web.js.

Copie exacte du pattern utilisé dans auth/comptes/whatsapp_client.py.
"""

import requests
from django.conf import settings


class WhatsAppDeliveryError(Exception):
    """L'envoi du message WhatsApp a échoué (service indisponible ou erreur réseau)."""


class WhatsAppWebClient:
    """Client HTTP vers le service whatsapp-web.js (whatsapp-service:3000).

    Le service Node.js gère la connexion WhatsApp via un compte dédié.
    Scanner le QR code une fois sur http://whatsapp-service:3000/qr
    pour activer l'envoi automatique.
    """

    def send(self, to_phone: str, message: str) -> None:
        """Envoie un message WhatsApp texte via le service Node.js.

        Lève WhatsAppDeliveryError si l'envoi échoue.
        """
        try:
            response = requests.post(
                f"{settings.WHATSAPP_SERVICE_URL}/send",
                json={"phone": to_phone, "message": message},
                headers={"X-Internal-Api-Key": settings.WHATSAPP_INTERNAL_API_KEY},
                timeout=15,
            )
            data = response.json()
        except requests.RequestException as exc:
            raise WhatsAppDeliveryError(f"Service WhatsApp inaccessible : {exc}") from exc

        if response.status_code == 503:
            raise WhatsAppDeliveryError("WhatsApp non connecté — scannez le QR code sur /qr pour activer l'envoi")

        if not data.get("success"):
            raise WhatsAppDeliveryError(data.get("error", "Erreur inconnue"))

    def send_with_pdf(self, to_phone: str, message: str, pdf_bytes: bytes, filename: str) -> None:
        """Envoie un PDF en pièce jointe WhatsApp avec un message en légende.

        Lève WhatsAppDeliveryError si l'envoi échoue.
        """
        import base64

        pdf_base64 = base64.b64encode(pdf_bytes).decode("utf-8")
        try:
            response = requests.post(
                f"{settings.WHATSAPP_SERVICE_URL}/send-with-pdf",
                json={"phone": to_phone, "message": message, "pdf_base64": pdf_base64, "filename": filename},
                headers={"X-Internal-Api-Key": settings.WHATSAPP_INTERNAL_API_KEY},
                timeout=30,
            )
            data = response.json()
        except requests.RequestException as exc:
            raise WhatsAppDeliveryError(f"Service WhatsApp inaccessible : {exc}") from exc

        if response.status_code == 503:
            raise WhatsAppDeliveryError("WhatsApp non connecté — scannez le QR code sur /qr pour activer l'envoi")

        if not data.get("success"):
            raise WhatsAppDeliveryError(data.get("error", "Erreur inconnue"))


whatsapp_client = WhatsAppWebClient()
