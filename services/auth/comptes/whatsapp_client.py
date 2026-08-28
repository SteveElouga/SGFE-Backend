"""Client HTTP vers le service whatsapp-web.js.

Copie assumée (voir ANO-014 dans docs/ETAT_DU_SYSTEME.md) du même pattern
que notification/notifications/whatsapp_client.py — chaque microservice
reste un projet Django strictement indépendant (voir CLAUDE.md racine),
donc pas de package partagé. Tout correctif apporté ici (ex. gestion d'un
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
            # Ce message atterrit tel quel dans le centre de notifications, sous
            # les yeux d'un comptable. Il y montrait « /qr » — une route interne
            # du service Node, que personne d'autre qu'un développeur ne peut
            # ouvrir. On nomme l'écran par lequel un administrateur y arrive.
            raise WhatsAppDeliveryError(
                "WhatsApp n'est pas connecté. Un administrateur doit lier le compte "
                "depuis Configuration › WhatsApp & Tokens."
            )

        # Ne suppose jamais que response.json() réussit avant d'avoir écarté
        # les statuts gérés explicitement (voir ANO-024) — un corps non-JSON
        # sur un statut inattendu romprait sinon la dégradation gracieuse.
        try:
            data = response.json()
        except ValueError as exc:
            raise WhatsAppDeliveryError(f"Réponse invalide du service WhatsApp (HTTP {response.status_code})") from exc

        if not data.get("success"):
            raise WhatsAppDeliveryError(data.get("error", "Erreur inconnue"))


whatsapp_client = WhatsAppWebClient()
