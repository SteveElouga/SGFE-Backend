#!/bin/sh
# Exécuté par le point d'entrée officiel postgres (docker-entrypoint-initdb.d/)
# UNE SEULE FOIS, au tout premier démarrage du PRIMAIRE (PGDATA encore vide).
#
# Crée le rôle de réplication et autorise les connexions de réplication
# depuis n'importe quel hôte du réseau Docker interne (0.0.0.0/0 est sans
# risque ici : ce réseau n'est pas exposé à l'hôte, voir docker-compose.yml
# — réseau "sgfe-internal" sans port publié pour les *-postgres).
#
# Voir docs : preuve de concept de réplication en streaming PostgreSQL
# (primaire + réplique), pour l'instant sur UNE base représentative
# (abonne-postgres/abonne-postgres-replica) — voir postgres/replication/README.md
# pour étendre aux 7 autres bases.
set -e

: "${REPLICATION_USER:?REPLICATION_USER manquant}"
: "${REPLICATION_PASSWORD:?REPLICATION_PASSWORD manquant}"

psql -v ON_ERROR_STOP=1 --username "$POSTGRES_USER" --dbname "$POSTGRES_DB" <<-EOSQL
    CREATE ROLE "${REPLICATION_USER}" WITH REPLICATION LOGIN PASSWORD '${REPLICATION_PASSWORD}';
EOSQL

echo "host replication ${REPLICATION_USER} 0.0.0.0/0 scram-sha-256" >> "$PGDATA/pg_hba.conf"

echo "[init-primary] Rôle de réplication '${REPLICATION_USER}' créé, pg_hba.conf mis à jour."
