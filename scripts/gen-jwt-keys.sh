#!/usr/bin/env bash
# Génère la paire de clés RSA pour la signature JWT RS256 de l'auth-service.
# Les clés sont GITIGNORÉES : à générer localement (une fois) et à ne JAMAIS
# committer. En production, elles proviennent du secrets manager / Azure Key
# Vault (monté en fichiers via le CSI Driver sur AKS).
#
# Usage :  ./scripts/gen-jwt-keys.sh            # -> services/auth/keys/
#          ./scripts/gen-jwt-keys.sh <dossier>  # dossier de sortie personnalisé
set -euo pipefail

DIR="${1:-services/auth/keys}"
mkdir -p "$DIR"

openssl genpkey -algorithm RSA -pkeyopt rsa_keygen_bits:2048 -out "$DIR/jwt_private.pem"
openssl rsa -in "$DIR/jwt_private.pem" -pubout -out "$DIR/jwt_public.pem"
chmod 600 "$DIR/jwt_private.pem"

echo "✅ Paire RSA générée dans $DIR/ (jwt_private.pem + jwt_public.pem) — gitignorée."
echo "   Ne jamais committer ces fichiers."
