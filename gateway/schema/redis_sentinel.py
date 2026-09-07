"""Client Redis asynchrone « Sentinel-aware » (voir redis/README.md à la racine du dépôt).

Sentinel bascule bien le maître Redis en cas de panne (3 instances, quorum
2/3), mais un service qui continue de se connecter à l'hôte fixe "redis" ne
suit pas cette bascule : à son retour, cet hôte est une RÉPLIQUE en lecture
seule. Les souscriptions de ce fichier ne font que SUBSCRIBE (jamais
d'écriture) — Redis réplique les PUBLISH vers ses répliques, donc ça
« marchait » même sur un hôte devenu réplique, tant qu'il restait un réplica
sain du vrai maître. Mais cette résilience est accidentelle : si l'hôte fixe
devient injoignable ou isolé, plus aucune souscription GraphQL ne reçoit
d'événement. Ce module redemande l'adresse du maître courant à Sentinel à
chaque connexion plutôt que de la coder en dur, comme le reste de la pile.
"""

from __future__ import annotations

from redis.asyncio import Redis
from redis.asyncio.sentinel import Sentinel
from django.conf import settings


async def get_redis_master(*, decode_responses: bool = False) -> Redis:
    """Retourne un client asynchrone connecté au maître Redis courant.

    Si `REDIS_SENTINELS` n'est pas défini (développement local hors Docker
    Compose, où Sentinel n'est pas forcément démarré), retombe sur
    `REDIS_URL` tel quel.
    """
    sentinels = getattr(settings, "REDIS_SENTINELS", "") or ""
    if not sentinels:
        return Redis.from_url(settings.REDIS_URL, decode_responses=decode_responses)  # type: ignore[no-any-return]

    hosts = [(host, int(port)) for host, port in (pair.split(":") for pair in sentinels.split(","))]
    sentinel = Sentinel(hosts, socket_connect_timeout=1)  # type: ignore[no-untyped-call]
    return sentinel.master_for(  # type: ignore[no-any-return]
        settings.REDIS_SENTINEL_MASTER, decode_responses=decode_responses
    )
