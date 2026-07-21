#!/usr/bin/env sh
# Sauvegarde des 8 bases PostgreSQL du SGFE : pg_dump gzip horodaté + rétention.
# Prévu pour tourner dans un conteneur postgres:16-alpine sur le réseau interne
# du compose (service `db-backup`) ; peut aussi se lancer à la main.
#
# Restauration d'un dump :
#   gunzip -c backups/<db>_<horodatage>.sql.gz \
#     | PGPASSWORD=... psql -h <db>-postgres -U <db>_user -d <db>
set -eu

BACKUP_DIR="${BACKUP_DIR:-/backups}"
RETENTION_DAYS="${RETENTION_DAYS:-7}"
: "${PGPASSWORD:?PGPASSWORD requis (mot de passe des bases)}"
export PGPASSWORD

mkdir -p "$BACKUP_DIR"
TS="$(date +%Y%m%d-%H%M%S)"

# host:db:user des 8 services. En prod, si les mots de passe diffèrent par base,
# fournir les credentials par base (ex. via un fichier ~/.pgpass monté).
DATABASES="
auth-postgres:auth_db:auth_user
abonne-postgres:abonne_db:abonne_user
campagne-postgres:campagne_db:campagne_user
facturation-postgres:facturation_db:facturation_user
paiement-postgres:paiement_db:paiement_user
notification-postgres:notification_db:notification_user
config-postgres:config_db:config_user
reporting-postgres:reporting_db:reporting_user
"

rc=0
for entry in $DATABASES; do
    host="${entry%%:*}"; rest="${entry#*:}"; db="${rest%%:*}"; user="${rest##*:}"
    out="$BACKUP_DIR/${db}_${TS}.sql.gz"
    if pg_dump -h "$host" -U "$user" -d "$db" | gzip > "$out"; then
        echo "[backup] OK   $db -> $(basename "$out")"
    else
        echo "[backup] FAIL $db" >&2
        rm -f "$out"; rc=1
    fi
done

# Rétention : supprimer les dumps plus vieux que RETENTION_DAYS jours.
find "$BACKUP_DIR" -name '*.sql.gz' -type f -mtime "+${RETENTION_DAYS}" -delete 2>/dev/null || true
echo "[backup] terminé $TS (rétention ${RETENTION_DAYS} j)"
exit $rc
