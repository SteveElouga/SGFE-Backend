"""Client HTTP vers le service whatsapp-web.js.

Copie assumée (voir ANO-014 dans docs/ETAT_DU_SYSTEME.md) du pattern
utilisé dans auth/comptes/whatsapp_client.py — chaque microservice reste
un projet Django strictement indépendant (voir CLAUDE.md racine), donc
pas de package partagé. Tout correctif apporté ici (ex. gestion d'un
nouveau code d'erreur du service Node) doit être répliqué manuellement
dans les deux copies.
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
        except requests.RequestException as exc:
            raise WhatsAppDeliveryError(f"Service WhatsApp inaccessible : {exc}") from exc

        if response.status_code == 503:
            raise WhatsAppDeliveryError("WhatsApp non connecté — scannez le QR code sur /qr pour activer l'envoi")

        # Le corps de réponse n'est garanti JSON qu'en cas de succès ou
        # d'erreur applicative (400/500) renvoyée par Express — un proxy/nginx
        # en amont pourrait renvoyer une page d'erreur HTML. On ne suppose
        # jamais que response.json() réussit avant d'avoir écarté les statuts
        # gérés explicitement (voir ANO-024).
        try:
            data = response.json()
        except ValueError as exc:
            raise WhatsAppDeliveryError(f"Réponse invalide du service WhatsApp (HTTP {response.status_code})") from exc

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
        except requests.RequestException as exc:
            raise WhatsAppDeliveryError(f"Service WhatsApp inaccessible : {exc}") from exc

        if response.status_code == 503:
            raise WhatsAppDeliveryError("WhatsApp non connecté — scannez le QR code sur /qr pour activer l'envoi")

        try:
            data = response.json()
        except ValueError as exc:
            raise WhatsAppDeliveryError(f"Réponse invalide du service WhatsApp (HTTP {response.status_code})") from exc

        if not data.get("success"):
            raise WhatsAppDeliveryError(data.get("error", "Erreur inconnue"))

    def get_qr(self) -> tuple[bool, str, str]:
        """Retourne (ready, qr_data_url, number, phase, depuis_ms) depuis le service Node.js.

        `ready` indique si WhatsApp est déjà connecté ; `qr` est une data-URL
        PNG à afficher (vide si connecté ou en cours d'initialisation) ;
        `number` est le numéro du compte appairé (présent uniquement si ready).
        Lève WhatsAppDeliveryError si le service est inaccessible ou renvoie
        une réponse invalide.
        """
        try:
            response = requests.get(
                f"{settings.WHATSAPP_SERVICE_URL}/qr-data",
                headers={"X-Internal-Api-Key": settings.WHATSAPP_INTERNAL_API_KEY},
                timeout=10,
            )
        except requests.RequestException as exc:
            raise WhatsAppDeliveryError(f"Service WhatsApp inaccessible : {exc}") from exc

        try:
            data = response.json()
        except ValueError as exc:
            raise WhatsAppDeliveryError(f"Réponse invalide du service WhatsApp (HTTP {response.status_code})") from exc

        return (
            bool(data.get("ready", False)),
            data.get("qr", "") or "",
            data.get("number", "") or "",
            data.get("phase", "") or "demarrage",
            int(data.get("depuis") or 0),
        )


whatsapp_client = WhatsAppWebClient()
