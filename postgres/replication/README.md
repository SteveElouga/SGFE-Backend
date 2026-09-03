# Réplication PostgreSQL — preuve de concept

## Ce qui existe

`abonne-postgres` (primaire) + `abonne-postgres-replica` (réplique en
streaming replication asynchrone), déclarés dans `docker-compose.yml`. Base
choisie comme représentative des 8 — le même patron s'applique à
`auth-postgres`, `campagne-postgres`, etc.

Mécanisme :
- `postgres/replication/init-primary.sh` (monté dans
  `docker-entrypoint-initdb.d/` du primaire) crée un rôle `replicator` et
  autorise les connexions de réplication dans `pg_hba.conf`, une seule fois,
  au premier démarrage.
- `postgres/replication/replica-entrypoint.sh` (remplace le point d'entrée
  officiel sur la réplique) clone le primaire via `pg_basebackup -R` si
  `PGDATA` est vide — l'option `-R` écrit `standby.signal` et
  `postgresql.auto.conf` (`primary_conninfo`), ce qui fait démarrer postgres
  en mode standby (lecture seule, applique le flux WAL reçu) plutôt qu'en
  base indépendante.

## Testé réellement (pas seulement démarré)

```bash
docker compose -p test-replication up -d abonne-postgres abonne-postgres-replica

docker exec test-replication-abonne-postgres-1 \
  psql -U abonne_user -d abonne_db -c \
  "CREATE TABLE replication_poc_test (id serial primary key, note text); \
   INSERT INTO replication_poc_test (note) VALUES ('ecrit-sur-le-primaire');"

docker exec test-replication-abonne-postgres-replica-1 \
  psql -U abonne_user -d abonne_db -c "SELECT * FROM replication_poc_test;"
# -> la ligne apparaît sur la réplique.

docker exec test-replication-abonne-postgres-replica-1 \
  psql -U abonne_user -d abonne_db -c "INSERT INTO replication_poc_test (note) VALUES ('x');"
# -> ERROR: cannot execute INSERT in a read-only transaction (attendu : la
#    réplique est en lecture seule, comme toute réplique de streaming).

docker exec test-replication-abonne-postgres-1 \
  psql -U abonne_user -d abonne_db -c \
  "SELECT application_name, state, sync_state FROM pg_stat_replication;"
# -> walreceiver | streaming | async
```

## Étendre aux 7 autres bases

Pour chaque `<service>-postgres` (auth, campagne, facturation, paiement,
notification, config, reporting) :

1. Sur le service primaire existant, ajouter les mêmes trois choses que sur
   `abonne-postgres` :
   - `environment: REPLICATION_USER / REPLICATION_PASSWORD`
   - `command:` avec `wal_level=replica -c max_wal_senders=10 -c
     max_replication_slots=10 -c hot_standby=on`
   - le montage de `postgres/replication/init-primary.sh` dans
     `docker-entrypoint-initdb.d/`
2. Déclarer `<service>-postgres-replica` sur le même modèle que
   `abonne-postgres-replica` (`PRIMARY_HOST: <service>-postgres`, montage de
   `replica-entrypoint.sh`, volume dédié).
3. Ajouter le volume `<service>_postgres_replica_data` en bas du fichier.

Les deux scripts sont génériques (aucun nom de base en dur) : ils se
réutilisent tels quels pour les 8 bases.

## Ce qu'une vraie haute disponibilité demanderait EN PLUS (honnêteté)

Ce POC prouve que la réplication fonctionne — il ne fait PAS de la bascule
automatique :

- **Pas de promotion automatique.** Si `abonne-postgres` tombe, la réplique
  reste une réplique : il faut lancer manuellement `SELECT pg_promote();`
  (ou `pg_ctl promote`) dessus. Un outil comme **Patroni**, **repmgr** ou
  **pg_auto_failover** est nécessaire pour détecter la panne et promouvoir
  automatiquement — aucun n'est en place ici.
- **Pas de découverte de service côté application.** Chaque service Django
  pointe sur un nom d'hôte fixe (`ABONNE_DB_HOST=abonne-postgres`, voir
  `docker-compose.yml`). Même avec une promotion automatique de la réplique,
  l'application continuerait d'écrire vers `abonne-postgres` (l'ancien
  primaire, potentiellement mort ou redevenu réplique) tant que cette
  variable n'est pas repointée — manuellement, ou via un composant
  supplémentaire (VIP flottante, pgBouncer/HAProxy avec health-check, ou le
  proxy intégré de Patroni) qui n'existe pas dans ce dépôt.
- **Réplication asynchrone.** `sync_state=async` : une transaction validée
  sur le primaire peut ne pas encore être arrivée sur la réplique au moment
  d'une panne — perte de données possible sur les toutes dernières
  transactions (fenêtre typiquement de l'ordre de la milliseconde en local,
  plus en présence de latence réseau réelle). Une réplication synchrone
  (`synchronous_commit`/`synchronous_standby_names`) éliminerait ce risque
  au prix de la latence d'écriture.

Ce POC est donc un point de départ concret pour la réplication — la
disponibilité réelle en cas de panne du primaire reste, aujourd'hui, un
sujet à part entière.
