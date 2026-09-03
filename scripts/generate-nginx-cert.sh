#!/usr/bin/env bash
# Génère un certificat TLS auto-signé pour le nginx de ce dépôt (le seul
# point d'entrée publié du backend), pour le développement local UNIQUEMENT.
#
# En production, ce script n'est PAS utilisé : la cible documentée dans
# docs/CHAINE_DE_LIVRAISON.md (§1, §12 étape 6) est CloudFront devant, avec
# Let's Encrypt sur l'origine EC2 — voir nginx/default.conf (bloc « --- PRODUCTION
# --- ») et scripts/renew-letsencrypt-cert.sh. Ce script auto-signé n'a
# vocation qu'à faire tourner `docker compose up` en local avec un nginx qui
# écoute réellement en HTTPS, sans dépendre d'un domaine public joignable par
# une autorité de certification.
#
# Les certificats sont GITIGNORÉS (comme les clés JWT RS256, voir
# scripts/gen-jwt-keys.sh) : à générer localement, jamais committés.
#
# Usage :  ./scripts/generate-nginx-cert.sh            # -> nginx/certs/
#          ./scripts/generate-nginx-cert.sh <dossier>  # dossier de sortie personnalisé
set -euo pipefail

DIR="${1:-nginx/certs}"
mkdir -p "$DIR"

DAYS="${NGINX_CERT_DAYS:-825}"  # 825 jours : plafond historique accepté par les navigateurs pour un certificat unique.

openssl req -x509 -nodes -newkey rsa:2048 \
    -keyout "$DIR/privkey.pem" \
    -out "$DIR/fullchain.pem" \
    -days "$DAYS" \
    -subj "/C=CM/O=SGFE (dev)/CN=localhost" \
    -addext "subjectAltName=DNS:localhost,IP:127.0.0.1"

chmod 600 "$DIR/privkey.pem"

echo "✅ Certificat auto-signé généré dans $DIR/ (fullchain.pem + privkey.pem) — gitignoré."
echo "   Le navigateur affichera un avertissement (certificat non reconnu) : normal en local."
echo "   Ne jamais utiliser ce script/ces fichiers en production — voir docs/CHAINE_DE_LIVRAISON.md."
