#!/usr/bin/env bash
# Synchronise la lib partagée d'isolation Postgres (`SET ROLE` sur un rôle
# `_runtime` non superutilisateur — voir libs/sgfe_common/sgfe_common/
# db_hardening.py pour la justification complète) vers chaque service qui
# l'utilise.
#
# Même choix de conception que scripts/sync-grpc-lib.sh (voir
# libs/sgfe_common/README.md) : chaque service est un projet Django
# indépendant avec son propre Dockerfile, dont le build context
# (docker-compose.yml) est scopé à son seul dossier (`build: ./services/X`)
# — aucun d'eux ne peut `COPY` un dossier situé hors de ce contexte. La
# source canonique reste donc un fichier unique, recopié tel quel vers
# chaque service qui en a besoin. La dérive entre copies est détectée par
# `--check` (comparaison de hash), pas empêchée par construction.
#
# Contrairement à sync-grpc-lib.sh (neuf destinations fixes, un composant
# gRPC = un besoin d'auth interne), la liste ci-dessous ne contient QUE les
# services qui ont effectivement une table à rendre immuable niveau base
# (aujourd'hui : paiement, facturation). Un futur service (campagne, abonné,
# auth, config — voir AUDIT_SGFE.md §8·J) ajoute sa propre ligne à
# DESTINATIONS quand il adopte ce mécanisme pour sa propre AuditLog — voir
# le "Comment un futur service adopte ce mécanisme" dans db_hardening.py.
#
# Usage :
#   ./scripts/sync-db-hardening-lib.sh            # recopie la source vers les destinations
#   ./scripts/sync-db-hardening-lib.sh --check    # vérifie l'absence de dérive (exit 1 si dérive), n'écrit rien
set -euo pipefail

ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
CANONICAL="$ROOT_DIR/libs/sgfe_common/sgfe_common/db_hardening.py"

DESTINATIONS=(
  "services/paiement/paiements/db_hardening.py"
  "services/facturation/factures/db_hardening.py"
)

BANNER=$'# ─────────────────────────────────────────────────────────────────────────\n# Fichier synchronisé — NE PAS ÉDITER DIRECTEMENT.\n#\n# Source canonique : libs/sgfe_common/sgfe_common/db_hardening.py\n# Après modification de la source, relancer : ./scripts/sync-db-hardening-lib.sh\n# Vérifier l\x27absence de dérive       : ./scripts/sync-db-hardening-lib.sh --check\n# ─────────────────────────────────────────────────────────────────────────\n'

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
    echo "  ./scripts/sync-db-hardening-lib.sh" >&2
    exit 1
  fi
  echo "OK — les copies correspondent à la source canonique."
fi
