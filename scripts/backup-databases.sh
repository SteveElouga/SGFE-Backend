#!/usr/bin/env sh
# Sauvegarde des 8 bases PostgreSQL du SGFE : pg_dump gzip chiffré horodaté +
# rétention + copie hors-site optionnelle. Prévu pour tourner dans un
# conteneur postgres:16-alpine sur le réseau interne du compose (service
# `db-backup`) ; peut aussi se lancer à la main. Nécessite `openssl` (déjà
# présent dans l'image postgres:16-alpine) et, si AWS_BACKUP_BUCKET est
# défini, le CLI `aws` (voir docker-compose.prod.yml).
#
# AWS_BACKUP_BUCKET : nom du bucket S3 provisionné par ansible/01-infra.yml
# (sortie "Bucket" en fin de run) — à reporter dans /opt/sgfe/.env en
# production. Vide en dev/CI : aucune copie hors-site tentée, comportement
# inchangé. Sans lui, un dump local survit tant que le disque de l'instance
# EC2 survit — voir l'écart documenté dans docs/PLAN_REPRISE_ACTIVITE.md.
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
        continue
    fi

    # Copie hors-site : sans ça, le dump local ci-dessus meurt avec l'instance
    # EC2 en cas de perte totale — voir docs/PLAN_REPRISE_ACTIVITE.md. Vide en
    # dev/CI (pas de bucket) : upload silencieusement sauté, comportement
    # inchangé. AWS_BACKUP_BUCKET n'exige aucune clé d'accès explicite : le
    # rôle d'instance EC2 (politique-instance.json.j2, IMDS) fournit déjà les
    # identifiants au CLI aws.
    if [ -n "${AWS_BACKUP_BUCKET:-}" ]; then
        if aws s3 cp "$out" "s3://${AWS_BACKUP_BUCKET}/$(basename "$out")" >/dev/null; then
            echo "[backup] S3   $db -> s3://${AWS_BACKUP_BUCKET}/$(basename "$out")"
        else
            echo "[backup] S3 FAIL $db (dump local conservé, non synchronisé)" >&2
            rc=1
        fi
    fi
done

# Rétention : supprimer les dumps plus vieux que RETENTION_DAYS jours.
find "$BACKUP_DIR" -name '*.sql.gz.enc' -type f -mtime "+${RETENTION_DAYS}" -delete 2>/dev/null || true
echo "[backup] terminé $TS (rétention ${RETENTION_DAYS} j)"
exit $rc
