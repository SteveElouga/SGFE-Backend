#!/usr/bin/env bash
# Installe les hooks Git du dépôt : pre-commit, commit-msg (Conventional Commits) et pre-push.
# Grâce à `default_install_hook_types` dans .pre-commit-config.yaml, une seule commande suffit.
#
# Prérequis : pre-commit (https://pre-commit.com)
#   pipx install pre-commit      # recommandé
#   # ou : pip install --user pre-commit
set -euo pipefail

if ! command -v pre-commit >/dev/null 2>&1; then
  echo "❌ 'pre-commit' est introuvable."
  echo "   Installez-le puis relancez : pipx install pre-commit   (ou pip install --user pre-commit)"
  exit 1
fi

# Installe les 3 types de hooks déclarés dans default_install_hook_types
pre-commit install --install-hooks

echo "✅ Hooks Git installés : pre-commit (ruff), commit-msg (commitizen), pre-push (ruff strict)."
echo "   Astuce : 'pre-commit run --all-files' pour tout vérifier maintenant."
