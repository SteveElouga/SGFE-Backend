"""Limite de débit globale des envois WhatsApp.

Contexte : le job de diffusion en lot (`schedulers.py`) impose déjà un
rythme volontaire à SES propres envois (5 messages/15s, voir
`_TAILLE_LOT`), mais les envois individuels immédiats — reçu de paiement,
rappel impayé, facture, message de test administrateur — partaient jusqu'ici
directement, sans aucune limite de débit globale. Un pic (plusieurs
paiements enregistrés d'affilée, par exemple) pouvait donc déclencher une
rafale de messages vers whatsapp-service, sur un compte WhatsApp Web
partagé par tout le système — un risque de bannissement.

Ce module impose un délai minimum entre deux envois WhatsApp consécutifs,
**tous déclencheurs confondus** (diffusion en lot ET envois individuels) :
il est appelé depuis `whatsapp_client.WhatsAppWebClient.send`/
`send_with_pdf`, le point de passage unique de tout message sortant, donc
sans avoir à modifier chaque appelant.

Mécanisme : verrou distribué via Redis (`REDIS_URL`, partagé par toutes les
instances du service) — un `SET ... NX PX` réserve un intervalle ; tant que
la clé n'a pas expiré, un nouvel envoi attend. Dégrade en verrou local (par
processus, `threading.Lock`) si Redis est inatteignable : protège alors au
moins ce processus, avec une garantie plus faible si le service tourne en
plusieurs répliques (documenté, pas silencieux — un warning est loggé).

Pas de throttling en environnement de test (`settings.TESTING`) : les tests
mockent `whatsapp_client` (ou `requests.post`), jamais de vrai réseau, et
imposer un vrai délai ralentirait la suite pour rien. Le mécanisme lui-même
est testé séparément (voir tests/test_rate_limiter.py) avec Redis mocké.
"""

from __future__ import annotations

import logging
import threading
import time
from typing import Any

from django.conf import settings

logger = logging.getLogger(__name__)

# Clé Redis partagée par toutes les instances du service : matérialise la
# réservation de l'intervalle en cours. Sa durée de vie (px) EST l'intervalle
# minimum : elle expire d'elle-même, pas besoin de la libérer explicitement.
_REDIS_KEY = "whatsapp:global:throttle"

# Attente totale maximale avant d'abandonner le respect strict de l'intervalle
# et d'envoyer quand même : un reçu de paiement qui n'arrive jamais parce que
# la limite de débit l'a bloqué indéfiniment serait pire que quelques envois
# légèrement rapprochés. Loggé quand ça arrive (ne devrait survenir qu'en cas
# de pic majeur et durable).
_MAX_WAIT_SECONDS = 30.0

_local_lock = threading.Lock()
_local_last_send_monotonic = 0.0


def throttle_whatsapp_send() -> None:
    """Bloque l'appelant jusqu'à ce que l'intervalle minimum depuis le
    dernier envoi WhatsApp (tous processus confondus) soit respecté.

    No-op si `WHATSAPP_RATE_LIMIT_MIN_INTERVAL_SECONDS` vaut 0 (throttling
    désactivé) ou en environnement de test.
    """
    if getattr(settings, "TESTING", False):
        return

    min_interval = getattr(settings, "WHATSAPP_RATE_LIMIT_MIN_INTERVAL_SECONDS", 3.0)
    if min_interval <= 0:
        return

    try:
        _throttle_via_redis(min_interval)
    except Exception as exc:  # Redis inatteignable, mal configuré, etc.
        logger.warning(
            "Rate-limit WhatsApp : Redis indisponible (%s), repli sur un verrou local "
            "(protection limitée à ce process si plusieurs répliques tournent).",
            exc,
        )
        _throttle_local(min_interval)


def _throttle_via_redis(min_interval: float) -> None:
    import redis

    client = redis.Redis.from_url(settings.REDIS_URL, socket_connect_timeout=1, socket_timeout=1)
    interval_ms = max(1, int(min_interval * 1000))
    deadline = time.monotonic() + _MAX_WAIT_SECONDS
    try:
        while True:
            if client.set(_REDIS_KEY, "1", nx=True, px=interval_ms):
                return
            if time.monotonic() >= deadline:
                logger.warning(
                    "Rate-limit WhatsApp : attente maximale de %.0fs dépassée, envoi sans "
                    "respecter l'intervalle minimum.",
                    _MAX_WAIT_SECONDS,
                )
                return
            # `: Any` — les stubs redis-py typent pttl/close en Awaitable[Any] | Any
            # (client sync ET async partagent la même signature de stub) ; sans
            # rapport avec un vrai bug, `redis.Redis.from_url` renvoie bien un
            # client synchrone ici.
            ttl_ms: Any = client.pttl(_REDIS_KEY)
            wait_seconds = (ttl_ms / 1000.0) if ttl_ms and ttl_ms > 0 else 0.05
            time.sleep(min(wait_seconds, min_interval))
    finally:
        client.close()  # type: ignore[no-untyped-call]


def _throttle_local(min_interval: float) -> None:
    global _local_last_send_monotonic
    with _local_lock:
        now = time.monotonic()
        wait_seconds = (_local_last_send_monotonic + min_interval) - now
        if wait_seconds > 0:
            time.sleep(wait_seconds)
        _local_last_send_monotonic = time.monotonic()
