#!/bin/sh
# Point d'entrée custom de la RÉPLIQUE en streaming replication.
#
# L'image officielle postgres ne sait faire qu'une chose au démarrage avec un
# PGDATA vide : `initdb` (crée une base neuve, indépendante). Pour une
# réplique, on veut l'inverse — un clonage binaire du PRIMAIRE via
# `pg_basebackup`, avec l'option `-R` qui écrit automatiquement
# `standby.signal` et `postgresql.auto.conf` (primary_conninfo) : c'est ce
# qui fait démarrer postgres en mode "standby" (lecture seule, applique le
# flux WAL reçu du primaire) plutôt qu'en primaire indépendant.
#
# Idempotent : si PGDATA n'est PAS vide (redémarrage du conteneur après un
# premier clonage réussi), on saute directement au démarrage normal —
# `pg_basebackup` ne doit tourner qu'une fois.
set -e

PGDATA="${PGDATA:-/var/lib/postgresql/data}"

: "${PRIMARY_HOST:?PRIMARY_HOST manquant}"
: "${REPLICATION_USER:?REPLICATION_USER manquant}"
: "${REPLICATION_PASSWORD:?REPLICATION_PASSWORD manquant}"

if [ -z "$(ls -A "$PGDATA" 2>/dev/null)" ]; then
    echo "[replica-entrypoint] PGDATA vide — clonage du primaire ${PRIMARY_HOST} via pg_basebackup..."

    tries=0
    until PGPASSWORD="$REPLICATION_PASSWORD" pg_basebackup \
        -h "$PRIMARY_HOST" -p "${PRIMARY_PORT:-5432}" \
        -U "$REPLICATION_USER" \
        -D "$PGDATA" -Fp -Xs -P -R; do
        tries=$((tries + 1))
        if [ "$tries" -ge 30 ]; then
            echo "[replica-entrypoint] Échec : primaire toujours injoignable après $tries tentatives." >&2
            exit 1
        fi
        echo "[replica-entrypoint] Primaire pas encore prêt (tentative $tries/30), nouvel essai dans 2s..."
        sleep 2
    done

    chmod 700 "$PGDATA"
    echo "[replica-entrypoint] Clonage terminé (standby.signal + primary_conninfo écrits par pg_basebackup -R)."
else
    echo "[replica-entrypoint] PGDATA déjà peuplé — démarrage direct en standby (pas de nouveau clonage)."
fi

exec docker-entrypoint.sh postgres
