"""Cache Redis court pour les lectures de Config Service.

`GetConfig` et `GetInfosSociete` sont interrogés en gRPC par tous les autres
services (facturation, paiement, notification) — potentiellement à chaque
requête (ex. `GetInfosSociete` à chaque reçu de paiement généré, voir
`RecuPaiementService` côté facturation-service). La donnée change pourtant
rarement (un admin modifie les infos société ou un paramètre quelques fois
par an) : un cache Redis court réduit la charge sans jamais risquer de servir
une valeur obsolète, puisque `UpdateConfig`/`UpdateInfosSociete` invalident
explicitement la clé modifiée avant de répondre.

Réutilise la même instance Redis que `event_publisher.py` (`settings.REDIS_URL`)
— aucune nouvelle dépendance. Best-effort et dégradé comme le reste du projet :
un Redis indisponible ne doit jamais faire échouer une lecture ou une écriture,
il fait simplement retomber sur la base (source de vérité).
"""

from __future__ import annotations

import json
import logging
from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    # Import réservé au typage : le code exécuté importe `redis` localement
    # dans chaque fonction (voir `_redis_client`), pour que les tests puissent
    # simuler son absence via `patch.dict(sys.modules, {"redis": None})`.
    import redis

logger = logging.getLogger(__name__)

# Court : quelques minutes suffisent à absorber l'essentiel du trafic (chaque
# reçu de paiement, chaque génération de facture) sans jamais laisser une
# valeur obsolète survivre longtemps si l'invalidation explicite venait à
# échouer (Redis indisponible pile au moment d'une mise à jour, par exemple).
TTL_SECONDS = 300

_CONFIG_KEY_PREFIX = "config:cache:param:"
_INFOS_SOCIETE_KEY = "config:cache:infos_societe"


def _redis_client() -> redis.Redis:
    from django.conf import settings
    import redis

    return redis.Redis.from_url(settings.REDIS_URL, decode_responses=True, socket_connect_timeout=1)


def get_cached_param(cle: str) -> dict[str, str] | None:
    """Valeur en cache d'un ConfigParam (dict compatible ConfigResponse), ou None."""
    try:
        r = _redis_client()
        # `: Any` — les stubs redis-py typent get() en Awaitable[Any] | Any
        # (client sync ET async partagent la même signature de stub) ; sans
        # rapport avec un vrai bug, `redis.Redis.from_url` renvoie bien un
        # client synchrone ici.
        raw: Any = r.get(_CONFIG_KEY_PREFIX + cle)
        r.close()  # type: ignore[no-untyped-call]  # redis-py : Redis.close() n'est pas annoté
        return json.loads(raw) if raw else None
    except Exception as exc:
        logger.warning("Cache Config ignoré (lecture, %s) : %s", cle, exc)
        return None


def set_cached_param(cle: str, data: dict[str, str]) -> None:
    """Met en cache le dict compatible ConfigResponse d'un paramètre."""
    try:
        r = _redis_client()
        r.setex(_CONFIG_KEY_PREFIX + cle, TTL_SECONDS, json.dumps(data))
        r.close()  # type: ignore[no-untyped-call]  # redis-py : Redis.close() n'est pas annoté
    except Exception as exc:
        logger.warning("Cache Config ignoré (écriture, %s) : %s", cle, exc)


def invalidate_param(cle: str) -> None:
    """Supprime la valeur en cache d'un paramètre — appelé par UpdateConfig.

    Invalidation explicite plutôt qu'attente du TTL : une mise à jour
    volontaire ne doit jamais rester masquée par une valeur en cache, même
    quelques secondes.
    """
    try:
        r = _redis_client()
        r.delete(_CONFIG_KEY_PREFIX + cle)
        r.close()  # type: ignore[no-untyped-call]  # redis-py : Redis.close() n'est pas annoté
    except Exception as exc:
        logger.warning("Cache Config ignoré (invalidation, %s) : %s", cle, exc)


def get_cached_infos_societe() -> dict[str, str] | None:
    """Valeur en cache des infos société (dict compatible InfosSocieteResponse), ou None."""
    try:
        r = _redis_client()
        raw: Any = r.get(_INFOS_SOCIETE_KEY)
        r.close()  # type: ignore[no-untyped-call]  # redis-py : Redis.close() n'est pas annoté
        return json.loads(raw) if raw else None
    except Exception as exc:
        logger.warning("Cache Config ignoré (lecture infos société) : %s", exc)
        return None


def set_cached_infos_societe(data: dict[str, str]) -> None:
    """Met en cache le dict compatible InfosSocieteResponse des infos société."""
    try:
        r = _redis_client()
        r.setex(_INFOS_SOCIETE_KEY, TTL_SECONDS, json.dumps(data))
        r.close()  # type: ignore[no-untyped-call]  # redis-py : Redis.close() n'est pas annoté
    except Exception as exc:
        logger.warning("Cache Config ignoré (écriture infos société) : %s", exc)


def invalidate_infos_societe() -> None:
    """Supprime les infos société en cache — appelé par UpdateInfosSociete."""
    try:
        r = _redis_client()
        r.delete(_INFOS_SOCIETE_KEY)
        r.close()  # type: ignore[no-untyped-call]  # redis-py : Redis.close() n'est pas annoté
    except Exception as exc:
        logger.warning("Cache Config ignoré (invalidation infos société) : %s", exc)
