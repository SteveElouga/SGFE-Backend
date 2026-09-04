"""Client HTTP vers le service whatsapp-web.js.

Copie assumée (voir ANO-014 dans docs/ETAT_DU_SYSTEME.md) du pattern
utilisé dans auth/comptes/whatsapp_client.py — chaque microservice reste
un projet Django strictement indépendant (voir CLAUDE.md racine), donc
pas de package partagé. Tout correctif apporté ici (ex. gestion d'un
nouveau code d'erreur du service Node) doit être répliqué manuellement
dans les deux copies.

── Garde-fou de test : WHATSAPP_DISABLE_SEND_FOR_TESTS ─────────────────────

`settings.WHATSAPP_DISABLE_SEND_FOR_TESTS` (variable d'environnement
`WHATSAPP_DISABLE_SEND_FOR_TESTS`, `"1"`/`"true"`) fait court-circuiter
`send()` et `send_with_pdf()` : aucun appel réseau vers `whatsapp-service`
n'est effectué, un succès est simulé immédiatement, et un log explicite
est émis (`"[TEST] envoi WhatsApp simulé, désactivé par
WHATSAPP_DISABLE_SEND_FOR_TESTS"`).

⚠️  RÉSERVÉ AUX ENVIRONNEMENTS DE TEST — JAMAIS EN PRODUCTION.
Le `whatsapp-service` de ce projet tourne avec un compte WhatsApp Web
RÉELLEMENT connecté (pas un bac à sable) : sans ce garde-fou, tout appel à
`send`/`send_with_pdf` part vers le numéro réel d'un abonné. Ce flag existe
uniquement pour permettre à des tests automatisés (notamment les tests e2e
Playwright du frontend qui enregistrent un paiement, lequel déclenche un
envoi de reçu automatique côté `paiement-service`) de s'exécuter contre une
stack backend vivante sans provoquer cet envoi.

La variable n'est **jamais** positionnée par défaut : elle est absente du
`docker-compose.yml` de base, donc le comportement de production ou de
développement normal reste totalement inchangé tant que personne ne la pose
explicitement.

Pour l'activer sur une stack de test locale, deux façons équivalentes :

  1. Dans `docker-compose.yml`, ajouter sous les `environment:` du service
     `notification-service` :
       WHATSAPP_DISABLE_SEND_FOR_TESTS: "1"
     (à ne faire que sur une copie/branche de test — ne jamais committer
     cette ligne dans la configuration de base du dépôt) ;

  2. Ponctuellement, sans modifier le fichier :
       docker compose run -e WHATSAPP_DISABLE_SEND_FOR_TESTS=1 \\
         notification-service <commande>
     ou, en relançant le service déjà défini dans le compose :
       WHATSAPP_DISABLE_SEND_FOR_TESTS=1 docker compose up -d notification-service
"""

import logging

import requests
from django.conf import settings

from notifications.rate_limiter import throttle_whatsapp_send

logger = logging.getLogger(__name__)


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

        Lève WhatsAppDeliveryError si l'envoi échoue. Simulé (aucun appel
        réseau) si `settings.WHATSAPP_DISABLE_SEND_FOR_TESTS` est activée —
        voir la docstring du module.
        """
        if settings.WHATSAPP_DISABLE_SEND_FOR_TESTS:
            logger.warning(
                "[TEST] envoi WhatsApp simulé, désactivé par WHATSAPP_DISABLE_SEND_FOR_TESTS",
                extra={"to": to_phone},
            )
            return

        # Limite de débit globale (voir rate_limiter.py) : protège le compte
        # WhatsApp Web partagé, quel que soit le déclencheur (envoi immédiat
        # ou lot de diffusion).
        throttle_whatsapp_send()
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

        Lève WhatsAppDeliveryError si l'envoi échoue. Simulé (aucun appel
        réseau) si `settings.WHATSAPP_DISABLE_SEND_FOR_TESTS` est activée —
        voir la docstring du module.
        """
        if settings.WHATSAPP_DISABLE_SEND_FOR_TESTS:
            # `filename` est un attribut réservé de `LogRecord` (le nom du
            # fichier source de l'appel) — le réutiliser dans `extra` lève
            # `KeyError` à l'émission du log. D'où `pdf_filename`.
            logger.warning(
                "[TEST] envoi WhatsApp simulé, désactivé par WHATSAPP_DISABLE_SEND_FOR_TESTS",
                extra={"to": to_phone, "pdf_filename": filename},
            )
            return

        import base64

        pdf_base64 = base64.b64encode(pdf_bytes).decode("utf-8")
        throttle_whatsapp_send()
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
            # Ce message atterrit tel quel dans le centre de notifications, sous
            # les yeux d'un comptable. Il y montrait « /qr » — une route interne
            # du service Node, que personne d'autre qu'un développeur ne peut
            # ouvrir. On nomme l'écran par lequel un administrateur y arrive.
            raise WhatsAppDeliveryError(
                "WhatsApp n'est pas connecté. Un administrateur doit lier le compte "
                "depuis Configuration › WhatsApp & Tokens."
            )

        try:
            data = response.json()
        except ValueError as exc:
            raise WhatsAppDeliveryError(f"Réponse invalide du service WhatsApp (HTTP {response.status_code})") from exc

        if not data.get("success"):
            raise WhatsAppDeliveryError(data.get("error", "Erreur inconnue"))

    def get_qr(self) -> tuple[bool, str, str, str, int]:
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
