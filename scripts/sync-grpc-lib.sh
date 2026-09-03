#!/usr/bin/env bash
# Synchronise la lib gRPC partagée (auth + mTLS) vers les neuf emplacements
# qui en avaient chacun une copie octet pour octet identique.
#
# Choix de conception — voir libs/sgfe_common/README.md pour la justification
# complète. En bref : chaque service est un projet Django indépendant avec
# son propre Dockerfile, dont le build context (docker-compose.yml) est
# scopé à son seul dossier (`build: ./services/X`) — aucun d'eux ne peut
# `COPY` un dossier situé hors de ce contexte. Plutôt qu'un vrai package
# Python installé (qui aurait exigé de restructurer le build context des 9
# services, ou de changer le nom du logger `__name__` que
# `services/paiement/paiements/tests/test_grpc_auth.py` vérifie
# explicitement), la source canonique reste un fichier unique, recopié tel
# quel vers chaque service. La dérive entre copies — le risque qu'un vrai
# package éliminerait — est ici détectée par `--check` (comparaison de hash),
# pas empêchée par construction : c'est le compromis assumé de cette
# approche.
#
# Usage :
#   ./scripts/sync-grpc-lib.sh            # recopie la source vers les 9 emplacements
#   ./scripts/sync-grpc-lib.sh --check    # vérifie l'absence de dérive (exit 1 si dérive), n'écrit rien
set -euo pipefail

ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
CANONICAL="$ROOT_DIR/libs/sgfe_common/sgfe_common/grpc_auth.py"

# Les neuf emplacements historiques (huit services + la gateway) — voir
# CLAUDE.md pour la liste des composants et AUDIT_SGFE.md:422 pour l'origine
# de ce chantier.
DESTINATIONS=(
  "services/abonne/abonnes/grpc_auth.py"
  "services/auth/comptes/grpc_auth.py"
  "services/campagne/campagnes/grpc_auth.py"
  "services/config/parametres/grpc_auth.py"
  "services/facturation/factures/grpc_auth.py"
  "services/notification/notifications/grpc_auth.py"
  "services/paiement/paiements/grpc_auth.py"
  "services/reporting/stats/grpc_auth.py"
  "gateway/schema/grpc_auth.py"
)

BANNER=$'# ─────────────────────────────────────────────────────────────────────────\n# Fichier synchronisé — NE PAS ÉDITER DIRECTEMENT.\n#\n# Source canonique : libs/sgfe_common/sgfe_common/grpc_auth.py\n# Après modification de la source, relancer : ./scripts/sync-grpc-lib.sh\n# Vérifier l\x27absence de dérive       : ./scripts/sync-grpc-lib.sh --check\n# ─────────────────────────────────────────────────────────────────────────\n'

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
    echo "  ./scripts/sync-grpc-lib.sh" >&2
    exit 1
  fi
  echo "OK — les neuf copies correspondent à la source canonique."
fi
