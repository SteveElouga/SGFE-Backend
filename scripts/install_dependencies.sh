#!/usr/bin/env bash
#
# Installe les dépendances de tous les microservices : pour chacun,
# crée (si besoin) un environnement virtuel dédié et installe son propre
# requirements.txt, autonome (chaque service reste indépendamment
# buildable/déployable, notamment en Docker).
#
# Usage :
#   ./scripts/install_dependencies.sh            # installe tous les services
#   ./scripts/install_dependencies.sh auth        # installe uniquement services/auth
#   ./scripts/install_dependencies.sh gateway     # installe uniquement gateway/

set -euo pipefail

ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
PYTHON_BIN="${PYTHON_BIN:-python3}"
ONLY_TARGET="${1:-}"

install_target() {
    local target_dir="$1"
    local target_name="$2"
    local req_file="${target_dir}/requirements.txt"

    if [[ ! -f "$req_file" ]]; then
        echo "==> ${target_name} : pas de requirements.txt, ignoré"
        return
    fi

    echo "==> ${target_name}"

    local venv_dir="${target_dir}/.venv"
    if [[ ! -d "$venv_dir" ]]; then
        echo "    Création de l'environnement virtuel..."
        "$PYTHON_BIN" -m venv "$venv_dir"
    fi

    # shellcheck disable=SC1091
    source "${venv_dir}/bin/activate"
    pip install --upgrade pip -q
    pip install -r "$req_file" -q
    deactivate

    echo "    OK"
}

echo "Installation des dépendances — Système de Gestion de Facturation d'Eau"
echo

if [[ -n "$ONLY_TARGET" ]]; then
    target_dir="${ROOT_DIR}/services/${ONLY_TARGET}"
    [[ -d "$target_dir" ]] || target_dir="${ROOT_DIR}/${ONLY_TARGET}"
    if [[ ! -d "$target_dir" ]]; then
        echo "Service ou dossier introuvable : ${ONLY_TARGET}" >&2
        exit 1
    fi
    install_target "$target_dir" "$(basename "$target_dir")"
    exit 0
fi

if [[ -d "${ROOT_DIR}/gateway" ]]; then
    install_target "${ROOT_DIR}/gateway" "gateway"
fi

for service_dir in "${ROOT_DIR}"/services/*/; do
    [[ -d "$service_dir" ]] || continue
    install_target "${service_dir%/}" "services/$(basename "$service_dir")"
done

echo
echo "Installation terminée."
