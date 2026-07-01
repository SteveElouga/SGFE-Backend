"""Client HTTP minimal vers l'API Brevo (SendinBlue) pour les emails admin."""

import logging
import os

import requests

logger = logging.getLogger(__name__)

_BREVO_API_URL = "https://api.brevo.com/v3/smtp/email"
_SENDER = {"name": "SGFE Notifications", "email": "noreply@sgfe.local"}
_TIMEOUT_S = 10


def envoyer_email_admin(to_email: str, subject: str, body: str) -> bool:
    """Envoie un email via Brevo à l'adresse admin configurée.

    Retourne True si l'envoi a réussi, False sinon (dégradation gracieuse).
    """
    api_key = os.environ.get("BREVO_API_KEY", "")
    if not api_key:
        logger.warning("BREVO_API_KEY non configurée — email admin ignoré")
        return False
    if not to_email:
        logger.debug("EMAIL_ADMIN_NOTIFICATIONS vide — email admin ignoré")
        return False

    try:
        response = requests.post(
            _BREVO_API_URL,
            headers={
                "api-key": api_key,
                "Content-Type": "application/json",
                "Accept": "application/json",
            },
            json={
                "sender": _SENDER,
                "to": [{"email": to_email}],
                "subject": subject,
                "textContent": body,
            },
            timeout=_TIMEOUT_S,
        )
        response.raise_for_status()
        logger.info("Email admin envoyé via Brevo", extra={"to": to_email, "subject": subject})
        return True
    except requests.RequestException as exc:
        logger.warning(
            "Envoi email admin Brevo échoué — dégradation gracieuse",
            extra={"to": to_email, "error": str(exc)},
        )
        return False
