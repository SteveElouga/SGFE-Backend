# Réplication Redis — preuve de concept (maître/réplique + Sentinel)

## Ce qui existe

- `redis` (maître, déjà présent) + `redis-replica` : réplication
  maître/réplique standard (`redis-server --replicaof redis 6379`).
- `redis-sentinel-1/2/3` : 3 instances Sentinel, quorum 2/3
  (`redis/sentinel.conf`), qui surveillent `mymaster` et élisent
  automatiquement un nouveau maître en cas de panne.

## Testé réellement — réplication

```bash
docker compose -p test-replication up -d redis redis-replica

docker exec test-replication-redis-1 redis-cli SET poc:replication "ecrit-sur-le-maitre"
docker exec test-replication-redis-replica-1 redis-cli GET poc:replication
# -> "ecrit-sur-le-maitre"

docker exec test-replication-redis-replica-1 redis-cli SET x y
# -> (error) READONLY You can't write against a read only replica.
```

## Testé réellement — Sentinel, PANNE PROVOQUÉE et bascule automatique

```bash
docker compose -p test-replication up -d redis-sentinel-1 redis-sentinel-2 redis-sentinel-3

docker exec test-replication-redis-sentinel-1-1 redis-cli -p 26379 SENTINEL master mymaster
# -> num-slaves:1  num-other-sentinels:2  quorum:2  (les 3 sentinels se sont bien découverts)

docker compose -p test-replication stop redis        # panne provoquée du maître

# logs sentinel (extrait réel) :
#   +sdown master mymaster redis 6379
#   +odown master mymaster redis 6379 #quorum 3/2
#   +switch-master mymaster redis 6379 172.29.0.4 6379   <- bascule vers la réplique

docker exec test-replication-redis-sentinel-1-1 redis-cli -p 26379 SENTINEL get-master-addr-by-name mymaster
# -> 172.29.0.4 (l'ancienne réplique)

docker exec test-replication-redis-replica-1 redis-cli SET poc:apres-failover ok
# -> OK : l'ancienne réplique accepte maintenant les écritures, elle est promue.

docker compose -p test-replication start redis        # l'ancien maître revient

docker exec test-replication-redis-1 redis-cli INFO replication | grep role
# -> role:slave  (Sentinel l'a automatiquement reconfiguré en réplique du nouveau maître)
```

C'est une bascule automatique **réelle**, pas simulée : détection de panne
par quorum, élection, promotion, ET réintégration automatique de l'ancien
maître comme réplique à son retour.

## Ce qu'une vraie haute disponibilité demanderait EN PLUS (honnêteté — le point important)

**Sentinel fonctionne, mais aucun service de ce dépôt ne le sait.** Tous les
services Django (voir `REDIS_URL=redis://redis:6379/0` dans chaque
`settings.py`/`docker-compose.yml`) et le service Node.js whatsapp-service se
connectent à Redis via le nom d'hôte fixe `redis`, avec un client Redis
standard (`redis.Redis.from_url(...)`) — **jamais** via un client
« Sentinel-aware » (`redis.sentinel.Sentinel(...)` côté Python,
`ioredis`/`sentinels` côté Node).

Conséquence concrète, vérifiable avec le scénario ci-dessus : après la
bascule, le nom `redis` continue de désigner l'ANCIEN maître — qui, une fois
revenu, est une RÉPLIQUE en lecture seule. Une application qui continuerait
à écrire sur `redis:6379` obtiendrait des erreurs `READONLY`, pas une
continuité de service. Sentinel a fait son travail (détecter, élire,
réintégrer) ; c'est la couche applicative qui ne le suit pas.

Pour une vraie HA Redis dans ce projet, il faudrait en plus :

1. **Changer le client dans les 8 services Django** (`event_publisher.py`
   de chaque service, `notifications/rate_limiter.py`,
   `notifications/schedulers.py`) pour interroger Sentinel
   (`Sentinel([("redis-sentinel-1", 26379), ...]).master_for("mymaster")`)
   plutôt qu'une URL fixe — et la session WhatsApp (`whatsapp-service/
   redis-store.js`, RemoteAuth) côté Node.js de la même façon.
2. **Ou** insérer un proxy Redis-aware (ex. un sidecar qui interroge
   Sentinel et réécrit l'adresse, ou HAProxy avec un health-check
   `redis-cli SENTINEL get-master-addr-by-name`) devant tous les clients,
   pour ne pas toucher au code applicatif.

Aucune des deux options n'est en place aujourd'hui : ce POC est un point de
départ pour la couche infrastructure, pas une haute disponibilité de bout en
bout.
