#!/usr/bin/env bash
# Configure la protection des branches `main` et `develop` (règle 7 de MEMORY.md) :
# push direct interdit, MR obligatoire, >= 1 revue, historique linéaire,
# force-push et suppression interdits, résolution des conversations requise.
#
# Prérequis : GitHub CLI authentifié avec droits ADMIN sur le dépôt :
#   gh auth login
#
# Usage :
#   ./scripts/setup-branch-protection.sh <owner>/<repo>
#   ex : ./scripts/setup-branch-protection.sh SteveElouga/SGFE-Backend
#        ./scripts/setup-branch-protection.sh SteveElouga/SGFE-Frontend
#
# NB : pour EXIGER une CI verte, renseignez les "contexts" (noms des jobs CI requis)
#      dans le corps JSON ci-dessous, puis relancez le script.
set -euo pipefail

REPO="${1:?Usage: $0 <owner>/<repo>  (ex: SteveElouga/SGFE-Backend)}"

if ! command -v gh >/dev/null 2>&1; then
  echo "❌ GitHub CLI 'gh' introuvable. Installez-le : https://cli.github.com/  puis 'gh auth login'."
  exit 1
fi

protect_branch () {
  local branch="$1"
  echo "🔒 Protection de '${branch}' sur ${REPO} ..."
  gh api -X PUT "repos/${REPO}/branches/${branch}/protection" \
    -H "Accept: application/vnd.github+json" \
    --input - <<'JSON'
{
  "required_status_checks": {
    "strict": true,
    "contexts": []
  },
  "enforce_admins": true,
  "required_pull_request_reviews": {
    "dismiss_stale_reviews": true,
    "require_code_owner_reviews": true,
    "required_approving_review_count": 1
  },
  "restrictions": null,
  "required_linear_history": true,
  "allow_force_pushes": false,
  "allow_deletions": false,
  "required_conversation_resolution": true
}
JSON
  echo "   ✅ '${branch}' protégée."
}

protect_branch main
protect_branch develop

echo
echo "✅ Terminé : 'main' et 'develop' sont protégées (push direct interdit, MR obligatoire,"
echo "   >= 1 revue via CODEOWNERS, historique linéaire, force-push/suppression interdits)."
echo "ℹ️  Pour exiger une CI verte, ajoutez vos jobs CI dans \"contexts\""
echo "   (ex : \"contexts\": [\"build\", \"tests\"]) puis relancez ce script."
