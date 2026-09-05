"""Throttle des demandes sensibles répétées (OTP téléphone, réinitialisation
de mot de passe par e-mail).

Contexte : ni `PhoneOtpService.request_otp_by_phone` ni
`PasswordSetupService.request_password_reset` n'imposaient jusqu'ici de
cooldown par identifiant (numéro de téléphone ou e-mail) — un client pouvait
déclencher un envoi WhatsApp ou e-mail à chaque appel, sans aucune limite
(« bombing »). Voir docs/CONFORMITE_SOC2_OWASP.md §3.1 A04, §3.2 API4/API6,
item #7 du plan de remédiation.

Mécanisme : verrou distribué via Redis (`REDIS_URL`, partagé par toutes les
instances du service) — un `SET ... NX EX` réserve la fenêtre de cooldown en
une seule commande atomique (pas de fenêtre de course entre un SETNX et un
EXPIRE séparés) ; si la clé existe déjà, la demande est refusée
(`ThrottleError`). Même patron que `notifications.rate_limiter` côté service
Notification (throttle **sortant** WhatsApp) : ce module en est l'équivalent
côté demandes **entrantes**.

Dégrade sur un verrou local (par processus) si Redis est inatteignable :
protège alors au moins ce processus, avec une garantie plus faible si le
service tourne en plusieurs répliques (documenté, pas silencieux — un
warning est loggé) — même compromis assumé que le rate-limiter WhatsApp.

Pas de throttle en environnement de test (`settings.TESTING`) par défaut :
les tests métier existants appellent ces flux avec des identifiants variés
sans avoir à composer avec une fenêtre de 60s, et ne mockent pas Redis. Le
mécanisme lui-même est testé séparément avec `TESTING=False` forcé et Redis
mocké (voir tests/test_throttle.py) — même convention que
`notifications.rate_limiter.throttle_whatsapp_send`.
"""

from __future__ import annotations

import logging
import threading
import time
from typing import Any

from django.conf import settings

logger = logging.getLogger(__name__)

# Fenêtre de cooldown par défaut : 1 demande / 60 secondes par identifiant.
FENETRE_THROTTLE_SECONDES = 60

_local_lock = threading.Lock()
# Dernière demande acceptée (temps monotone) par clé — repli local si Redis
# est inatteignable. Partagé par tout le process (répliques non couvertes).
_local_dernieres_demandes: dict[str, float] = {}


class ThrottleError(Exception):
    """Levée quand une demande sensible est répétée avant la fin du cooldown."""


def verifier_throttle(cle: str, fenetre_secondes: int = FENETRE_THROTTLE_SECONDES) -> None:
    """Impose un cooldown de `fenetre_secondes` entre deux demandes portant la même clé.

    Lève `ThrottleError` si une demande portant la même clé a déjà été acceptée
    il y a moins de `fenetre_secondes`. No-op en environnement de test
    (`settings.TESTING`) — voir le docstring du module.
    """
    if getattr(settings, "TESTING", False):
        return

    try:
        _verifier_via_redis(cle, fenetre_secondes)
    except ThrottleError:
        raise
    except Exception as exc:  # Redis inatteignable, mal configuré, etc.
        logger.warning(
            "Throttle %s : Redis indisponible (%s), repli sur un verrou local "
            "(protection limitée à ce process si plusieurs répliques tournent).",
            cle,
            exc,
        )
        _verifier_local(cle, fenetre_secondes)


def _verifier_via_redis(cle: str, fenetre_secondes: int) -> None:
    """Réserve la fenêtre de cooldown dans Redis via un `SET NX EX` atomique."""
    import redis

    client = redis.Redis.from_url(settings.REDIS_URL, socket_connect_timeout=1, socket_timeout=1)
    try:
        # `: Any` — les stubs redis-py typent set() en Awaitable[Any] | Any
        # (client sync ET async partagent la même signature de stub) ; sans
        # rapport avec un vrai bug, `redis.Redis.from_url` renvoie bien un
        # client synchrone ici (même réserve que config/cache.py et
        # notifications/rate_limiter.py).
        acquis: Any = client.set(cle, "1", nx=True, ex=fenetre_secondes)
        if not acquis:
            raise ThrottleError(f"Trop de demandes, réessayez dans au plus {fenetre_secondes} secondes")
    finally:
        client.close()  # type: ignore[no-untyped-call]  # redis-py : Redis.close() n'est pas annoté


def _verifier_local(cle: str, fenetre_secondes: int) -> None:
    """Repli en mémoire process (verrou local) quand Redis est inatteignable."""
    now = time.monotonic()
    with _local_lock:
        derniere = _local_dernieres_demandes.get(cle)
        if derniere is not None and now - derniere < fenetre_secondes:
            raise ThrottleError(f"Trop de demandes, réessayez dans au plus {fenetre_secondes} secondes")
        _local_dernieres_demandes[cle] = now
