#!/usr/bin/env bash
# Obtient/renouvelle le certificat Let's Encrypt de l'origine nginx en
# production (voir docs/CHAINE_DE_LIVRAISON.md §1, §12 étape 6 : CloudFront
# devant avec TLS public ACM, Let's Encrypt sur cette origine EC2).
#
# Méthode HTTP-01 en webroot : certbot dépose un fichier de défi dans
# CERTBOT_WEBROOT_DIR, que le nginx de ce dépôt sert sur :80/.well-known/
# acme-challenge/ (voir nginx/default.conf) — nginx doit donc déjà tourner et
# être joignable sur le port 80 du domaine avant le premier lancement.
#
# Statut : point de départ concret, PAS encore branché sur un cron/systemd
# timer — cette automatisation revient à Ansible (amorçage machine, encore à
# écrire, voir ansible/02-bootstrap.yml et le tableau de
# docs/CHAINE_DE_LIVRAISON.md §12). En attendant, exécuter ce script
# manuellement puis `docker compose ... restart nginx` sur la machine cible.
#
# Usage :
#   LETSENCRYPT_DOMAIN=api.sgfe.example.com \
#   LETSENCRYPT_EMAIL=admin@sgfe.example.com \
#   ./scripts/renew-letsencrypt-cert.sh
set -euo pipefail

DOMAIN="${LETSENCRYPT_DOMAIN:?LETSENCRYPT_DOMAIN manquant (ex. api.sgfe.example.com)}"
EMAIL="${LETSENCRYPT_EMAIL:?LETSENCRYPT_EMAIL manquant (contact requis par Let's Encrypt)}"
WEBROOT="${CERTBOT_WEBROOT_DIR:-/var/www/sgfe-certbot}"
CERT_DIR="${LETSENCRYPT_CERT_DIR:-/etc/letsencrypt/live/$DOMAIN}"

mkdir -p "$WEBROOT"

certbot certonly \
    --non-interactive --agree-tos \
    --webroot -w "$WEBROOT" \
    -d "$DOMAIN" \
    -m "$EMAIL"

echo "✅ Certificat Let's Encrypt prêt dans $CERT_DIR/ (fullchain.pem + privkey.pem)."
echo "   Recharger nginx pour prendre en compte un renouvellement :"
echo "   docker compose -f docker-compose.yml -f docker-compose.prod.yml exec nginx nginx -s reload"
