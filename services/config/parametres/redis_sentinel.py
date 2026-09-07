"""Client Redis « Sentinel-aware » (voir redis/README.md à la racine du dépôt).

Sentinel bascule bien le maître Redis en cas de panne (3 instances, quorum
2/3), mais un service qui continue de se connecter à l'hôte fixe "redis" ne
suit pas cette bascule : à son retour, cet hôte est une RÉPLIQUE en lecture
seule. Bug réel constaté le 07/09/2026 — après un redémarrage du conteneur
"redis", Sentinel avait promu "redis-replica" comme prévu, mais tous les
appels de ce service continuaient d'écrire sur "redis" et échouaient avec
`READONLY You can't write against a read only replica.`

Ce module redemande l'adresse du maître courant à Sentinel à chaque appel
plutôt que de la coder en dur — même best-effort et mêmes délais courts que
le reste des appels Redis de ce service (voir event_publisher.py).
"""

from __future__ import annotations

import redis
from django.conf import settings
from redis.sentinel import Sentinel


def get_redis_master(
    *,
    decode_responses: bool = False,
    socket_connect_timeout: float = 1,
    socket_timeout: float | None = None,
) -> redis.Redis:
    """Retourne un client connecté au maître Redis courant.

    Si `REDIS_SENTINELS` n'est pas défini (développement local hors Docker
    Compose, où Sentinel n'est pas forcément démarré), retombe sur
    `REDIS_URL` tel quel — même hôte fixe qu'avant, mais seulement pour ce
    cas-là.
    """
    kwargs: dict[str, object] = {
        "socket_connect_timeout": socket_connect_timeout,
        "decode_responses": decode_responses,
    }
    if socket_timeout is not None:
        kwargs["socket_timeout"] = socket_timeout

    sentinels = getattr(settings, "REDIS_SENTINELS", "") or ""
    if not sentinels:
        return redis.Redis.from_url(settings.REDIS_URL, **kwargs)

    hosts = [(host, int(port)) for host, port in (pair.split(":") for pair in sentinels.split(","))]
    sentinel = Sentinel(hosts, socket_connect_timeout=socket_connect_timeout)  # type: ignore[no-untyped-call]
    return sentinel.master_for(settings.REDIS_SENTINEL_MASTER, **kwargs)  # type: ignore[no-untyped-call,no-any-return]
