#!/usr/bin/env sh
# Restore drill : vérifie qu'une sauvegarde produite par backup-databases.sh
# est RÉELLEMENT restaurable, pas seulement produite.
#
# Prend la sauvegarde chiffrée la plus récente d'une base représentative
# (config_db par défaut — la plus petite des 8), la déchiffre, la restaure
# dans une base jetable sur un conteneur Postgres TEMPORAIRE démarré par ce
# script (jamais sur une base de dev existante), vérifie par un contrôle
# simple (tables + comptage de lignes) que la restauration a fonctionné, puis
# détruit le conteneur temporaire.
#
# Usage :
#   BACKUP_ENCRYPTION_KEY=... ./scripts/test-restore.sh [nom_base] [dossier_backups]
#
# nom_base        : défaut config_db
# dossier_backups : défaut ./backups (relatif à la racine du dépôt)
#
# Ne touche à aucun conteneur ni base existants : le conteneur Postgres utilisé
# pour la restauration est créé (docker run --name sgfe-restore-drill-<pid>)
# puis supprimé (docker rm -f) en fin de script, succès ou échec.
set -eu

SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
REPO_ROOT="$(cd "$SCRIPT_DIR/.." && pwd)"

DB_NAME="${1:-config_db}"
BACKUP_DIR="${2:-$REPO_ROOT/backups}"
RESTORE_DB="${DB_NAME}_restore_test"

: "${BACKUP_ENCRYPTION_KEY:?BACKUP_ENCRYPTION_KEY requis pour déchiffrer la sauvegarde (voir .env.example)}"

CONTAINER_NAME="sgfe-restore-drill-$$"
PG_IMAGE="postgres:16-alpine"
PG_USER="restore_test_user"
PG_PASSWORD="restore-test-$$"

tmp_sql=""

cleanup() {
    [ -n "$tmp_sql" ] && rm -f "$tmp_sql"
    if docker ps -aq -f "name=^${CONTAINER_NAME}\$" | grep -q .; then
        echo "[restore-test] Nettoyage : suppression du conteneur $CONTAINER_NAME"
        docker rm -f "$CONTAINER_NAME" >/dev/null 2>&1 || true
    fi
}
trap cleanup EXIT INT TERM

echo "[restore-test] Base ciblée      : $DB_NAME"
echo "[restore-test] Dossier backups  : $BACKUP_DIR"

# ── 1. Sélectionner la sauvegarde chiffrée la plus récente ──────────────────
latest="$(ls -t "$BACKUP_DIR"/"${DB_NAME}"_*.sql.gz.enc 2>/dev/null | head -n1 || true)"
if [ -z "$latest" ]; then
    echo "[restore-test] ÉCHEC : aucune sauvegarde .sql.gz.enc pour '$DB_NAME' dans $BACKUP_DIR" >&2
    exit 1
fi
echo "[restore-test] Sauvegarde sélectionnée : $(basename "$latest")"

# ── 2. Déchiffrer + décompresser dans un fichier temporaire ─────────────────
tmp_sql="$(mktemp)"
if ! openssl enc -d -aes-256-cbc -pbkdf2 -salt -pass env:BACKUP_ENCRYPTION_KEY \
        -in "$latest" | gunzip -c > "$tmp_sql"; then
    echo "[restore-test] ÉCHEC : déchiffrement/décompression (mauvaise passphrase ou fichier corrompu ?)" >&2
    exit 1
fi
sql_bytes="$(wc -c < "$tmp_sql" | tr -d ' ')"
echo "[restore-test] Déchiffré : $sql_bytes octets de SQL en clair"

# ── 3. Démarrer un conteneur Postgres jetable ────────────────────────────────
echo "[restore-test] Démarrage du conteneur Postgres jetable : $CONTAINER_NAME"
docker run -d --name "$CONTAINER_NAME" \
    -e POSTGRES_USER="$PG_USER" \
    -e POSTGRES_PASSWORD="$PG_PASSWORD" \
    -e POSTGRES_DB=postgres \
    "$PG_IMAGE" >/dev/null

echo "[restore-test] Attente de la disponibilité de Postgres..."
i=0
until docker exec "$CONTAINER_NAME" pg_isready -U "$PG_USER" >/dev/null 2>&1; do
    i=$((i + 1))
    if [ "$i" -gt 30 ]; then
        echo "[restore-test] ÉCHEC : timeout en attendant Postgres" >&2
        docker logs "$CONTAINER_NAME" >&2 || true
        exit 1
    fi
    sleep 1
done
echo "[restore-test] Postgres prêt (après ${i}s)."

# ── 4. Créer la base jetable et y restaurer le dump ─────────────────────────
docker exec "$CONTAINER_NAME" psql -U "$PG_USER" -d postgres -v ON_ERROR_STOP=1 \
    -c "CREATE DATABASE ${RESTORE_DB};" >/dev/null
echo "[restore-test] Base jetable créée : $RESTORE_DB"

# pg_dump (sans --no-owner, comme backup-databases.sh) embarque des
# `ALTER ... OWNER TO <role>` / `GRANT ... TO <role>` qui référencent le rôle
# applicatif du service d'origine (ex. config_user). Ce rôle n'existe pas sur
# le Postgres jetable — on le crée à la volée, idempotent, pour tous les rôles
# effectivement référencés dans le dump plutôt que de deviner un nom.
roles="$(grep -oE '(OWNER TO|TO) [A-Za-z_][A-Za-z0-9_]*' "$tmp_sql" \
    | awk '{print $NF}' | sort -u || true)"
for role in $roles; do
    docker exec "$CONTAINER_NAME" psql -U "$PG_USER" -d postgres -tAc \
        "SELECT 1 FROM pg_roles WHERE rolname = '${role}';" | grep -q 1 || {
        echo "[restore-test] Création du rôle référencé par le dump : $role"
        docker exec "$CONTAINER_NAME" psql -U "$PG_USER" -d postgres -v ON_ERROR_STOP=1 \
            -c "CREATE ROLE \"${role}\";" >/dev/null
    }
done

restore_log="$(mktemp)"
if ! docker exec -i "$CONTAINER_NAME" psql -U "$PG_USER" -d "$RESTORE_DB" -v ON_ERROR_STOP=1 \
        < "$tmp_sql" > "$restore_log" 2>&1; then
    echo "[restore-test] ÉCHEC pendant la restauration :" >&2
    cat "$restore_log" >&2
    rm -f "$restore_log"
    exit 1
fi
rm -f "$restore_log"
echo "[restore-test] Restauration psql terminée sans erreur."

# ── 5. Vérification : tables présentes + comptage de lignes par table ───────
table_count="$(docker exec "$CONTAINER_NAME" psql -U "$PG_USER" -d "$RESTORE_DB" -tAc \
    "SELECT count(*) FROM information_schema.tables WHERE table_schema = 'public';" | tr -d ' ')"
echo "[restore-test] Tables restaurées dans le schéma public : $table_count"

if [ "$table_count" -eq 0 ]; then
    echo "[restore-test] ÉCHEC : la restauration n'a produit aucune table" >&2
    exit 1
fi

echo "[restore-test] Détail (table -> nombre de lignes) :"
docker exec "$CONTAINER_NAME" psql -U "$PG_USER" -d "$RESTORE_DB" -tAc "
SELECT table_name || ' -> ' ||
       (xpath('/row/c/text()',
              query_to_xml(format('SELECT count(*) AS c FROM %I', table_name), false, true, '')
       ))[1]::text
FROM information_schema.tables
WHERE table_schema = 'public'
ORDER BY table_name;
"

echo "[restore-test] SUCCÈS : sauvegarde '$latest' restaurée et vérifiée ($table_count tables) dans '$RESTORE_DB' sur un Postgres jetable."
# Le trap EXIT se charge de supprimer le conteneur temporaire et le fichier SQL déchiffré.
