#!/usr/bin/env sh
# Sauvegarde des 8 bases PostgreSQL du SGFE : pg_dump gzip chiffré horodaté +
# rétention. Prévu pour tourner dans un conteneur postgres:16-alpine sur le
# réseau interne du compose (service `db-backup`) ; peut aussi se lancer à la
# main. Nécessite `openssl` (déjà présent dans l'image postgres:16-alpine).
#
# Chaque dump est chiffré symétriquement (AES-256-CBC, KDF PBKDF2, sel) avec
# la passphrase BACKUP_ENCRYPTION_KEY avant d'être écrit sur disque — le
# fichier `.sql.gz.enc` seul ne suffit pas à restaurer une base : les backups
# vivent sur le même disque que les données qu'ils protègent (voir
# docs/CHAINE_DE_LIVRAISON.md §13.3), donc au moins le contenu ne fuite pas
# avec un volume ou une machine volée.
#
# Restauration d'un dump :
#   openssl enc -d -aes-256-cbc -pbkdf2 -salt \
#       -pass env:BACKUP_ENCRYPTION_KEY \
#       -in backups/<db>_<horodatage>.sql.gz.enc \
#     | gunzip -c \
#     | PGPASSWORD=... psql -h <db>-postgres -U <db>_user -d <db>
#
# (voir aussi scripts/test-restore.sh, qui automatise et vérifie ce parcours
# sur un conteneur Postgres jetable)
set -eu

BACKUP_DIR="${BACKUP_DIR:-/backups}"
RETENTION_DAYS="${RETENTION_DAYS:-7}"
: "${PGPASSWORD:?PGPASSWORD requis (mot de passe des bases)}"
: "${BACKUP_ENCRYPTION_KEY:?BACKUP_ENCRYPTION_KEY requis (passphrase de chiffrement des sauvegardes — voir .env.example)}"
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
    out="$BACKUP_DIR/${db}_${TS}.sql.gz.enc"
    if pg_dump -h "$host" -U "$user" -d "$db" \
        | gzip \
        | openssl enc -aes-256-cbc -pbkdf2 -salt -pass env:BACKUP_ENCRYPTION_KEY \
        > "$out"; then
        echo "[backup] OK   $db -> $(basename "$out")"
    else
        echo "[backup] FAIL $db" >&2
        rm -f "$out"; rc=1
    fi
done

# Rétention : supprimer les dumps plus vieux que RETENTION_DAYS jours.
find "$BACKUP_DIR" -name '*.sql.gz.enc' -type f -mtime "+${RETENTION_DAYS}" -delete 2>/dev/null || true
echo "[backup] terminé $TS (rétention ${RETENTION_DAYS} j)"
exit $rc
