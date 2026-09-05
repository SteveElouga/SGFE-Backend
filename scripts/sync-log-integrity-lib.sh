#!/usr/bin/env bash
# Synchronise la lib partagée de chaînage de hash des logs (tamper-evidence —
# voir libs/sgfe_common/sgfe_common/log_integrity.py pour la justification
# complète, AUDIT_SGFE.md §J "Journalisation de sécurité centralisée et
# inviolable") vers chaque service qui l'utilise.
#
# Même choix de conception que sync-grpc-lib.sh / sync-db-hardening-lib.sh
# (voir libs/sgfe_common/README.md) : chaque service est un projet Django
# indépendant avec son propre Dockerfile, dont le build context
# (docker-compose.yml) est scopé à son seul dossier (`build: ./services/X`)
# — aucun d'eux ne peut `COPY` un dossier situé hors de ce contexte. La
# source canonique reste donc un fichier unique, recopié tel quel vers
# chaque service qui en a besoin. La dérive entre copies est détectée par
# `--check` (comparaison de hash), pas empêchée par construction.
#
# Contrairement à sync-grpc-lib.sh (neuf destinations fixes), et comme
# sync-db-hardening-lib.sh, la liste ci-dessous ne contient QUE les
# composants effectivement câblés à ce mécanisme aujourd'hui : Auth et
# Gateway, les deux points d'entrée les plus sensibles pour la sécurité
# (voir AUDIT_SGFE.md §J). Étendre aux 7 autres composants est une simple
# répétition du même câblage — ajouter sa propre ligne à DESTINATIONS quand
# un futur composant l'adopte.
#
# Usage :
#   ./scripts/sync-log-integrity-lib.sh            # recopie la source vers les destinations
#   ./scripts/sync-log-integrity-lib.sh --check    # vérifie l'absence de dérive (exit 1 si dérive), n'écrit rien
set -euo pipefail

ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
CANONICAL="$ROOT_DIR/libs/sgfe_common/sgfe_common/log_integrity.py"

DESTINATIONS=(
  "services/auth/comptes/log_integrity.py"
  "gateway/schema/log_integrity.py"
)

BANNER=$'# ─────────────────────────────────────────────────────────────────────────\n# Fichier synchronisé — NE PAS ÉDITER DIRECTEMENT.\n#\n# Source canonique : libs/sgfe_common/sgfe_common/log_integrity.py\n# Après modification de la source, relancer : ./scripts/sync-log-integrity-lib.sh\n# Vérifier l\x27absence de dérive       : ./scripts/sync-log-integrity-lib.sh --check\n# ─────────────────────────────────────────────────────────────────────────\n'

if [[ ! -f "$CANONICAL" ]]; then
  echo "Source canonique introuvable : $CANONICAL" >&2
  exit 1
fi

mode="sync"
if [[ "${1:-}" == "--check" ]]; then
  mode="check"
fi

expected_hash="$(
  { printf '%s' "$BANNER"; cat "$CANONICAL"; } | shasum -a 256 | cut -d' ' -f1
)"

drift=0
for rel in "${DESTINATIONS[@]}"; do
  dest="$ROOT_DIR/$rel"

  if [[ "$mode" == "check" ]]; then
    if [[ ! -f "$dest" ]]; then
      echo "MANQUANT : $rel"
      drift=1
      continue
    fi
    actual_hash="$(shasum -a 256 "$dest" | cut -d' ' -f1)"
    if [[ "$actual_hash" != "$expected_hash" ]]; then
      echo "DÉRIVE   : $rel (ne correspond plus à la source canonique)"
      drift=1
    fi
  else
    mkdir -p "$(dirname "$dest")"
    { printf '%s' "$BANNER"; cat "$CANONICAL"; } > "$dest"
    echo "sync : $rel"
  fi
done

if [[ "$mode" == "check" ]]; then
  if [[ "$drift" -ne 0 ]]; then
    echo
    echo "Des copies ont dérivé de la source canonique. Lancer :" >&2
    echo "  ./scripts/sync-log-integrity-lib.sh" >&2
    exit 1
  fi
  echo "OK — les copies correspondent à la source canonique."
fi
