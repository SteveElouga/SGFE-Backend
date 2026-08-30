#!/usr/bin/env bash
# Seed de démo SGFE — jeu de test multi-mois pour le dashboard / statsParMois.
#
# Prérequis : la stack tourne et les migrations sont passées (docker compose up -d).
# Idempotent : ré-exécutable sans doublons (UUID déterministes via uuid5).
#
#   bash scripts/seed_demo.sh
#
# Crée 4 comptes (mot de passe commun : Demo1234!) :
#   demo_admin (ADMIN) · demo_comptable (COMPTABLE) · demo_superviseur (SUPERVISEUR) · demo_agent (AGENT)
# + 3 campagnes (2 au superviseur, 1 à l'admin), 5 factures sur 3 mois,
#   4 paiements (dont 1 dissocié dans le temps et 1 annulé).
#
# On passe chaque script via `manage.py shell -c "$(cat …)"` (exec en un bloc :
# robuste aux lignes vides et aux def, contrairement au pipe stdin du REPL).
set -euo pipefail
cd "$(dirname "$0")/.."

run() {  # run <service-compose> <script.py>
  echo "→ $2"
  docker compose exec -T "$1" python manage.py shell -c "$(cat "$2")"
}

run auth-service        scripts/seed/auth.py
run campagne-service    scripts/seed/campagne.py
run facturation-service scripts/seed/facturation.py
run paiement-service    scripts/seed/paiement.py

echo
echo "✓ Seed terminé."
echo "  Connexion (identifier = username), mot de passe : Demo1234!"
echo "  demo_admin · demo_comptable · demo_superviseur · demo_agent"
