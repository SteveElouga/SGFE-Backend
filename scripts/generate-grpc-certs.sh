#!/usr/bin/env bash
# Génère la CA interne et le certificat mTLS partagé par les neuf composants
# gRPC (les huit services + la gateway).
#
# Choix de conception — un seul certificat, pas neuf. Les SAN du certificat
# serveur couvrent tous les noms d'hôtes internes déclarés dans
# docker-compose.yml (auth-service, abonne-service, campagne-service,
# facturation-service, paiement-service, notification-service,
# reporting-service, config-service, gateway). Ce même certificat est
# réutilisé comme identité CLIENTE (canal_authentifie côté
# */grpc_auth.py) : les neuf composants sont des pairs égaux d'un même
# maillage interne, et rien dans ce chantier n'a besoin de distinguer
# "facturation-service qui appelle" de "paiement-service qui appelle" au
# niveau TLS — c'est déjà le rôle de INTERNAL_GRPC_KEY (voir grpc_auth.py),
# qui authentifie l'appelant applicatif et reste inchangé. Un certificat par
# service permettrait une révocation individuelle plus fine, au prix de neuf
# clés privées à faire tourner ; si ce cloisonnement devient nécessaire plus
# tard, ce script est le seul endroit à faire évoluer.
#
# Les fichiers produits sont GITIGNORÉS : à générer localement (une fois par
# environnement) et à ne JAMAIS committer — même traitement que les clés JWT
# (scripts/gen-jwt-keys.sh) et les .env.
#
# Usage :  ./scripts/generate-grpc-certs.sh            # -> certs/
#          ./scripts/generate-grpc-certs.sh <dossier>  # dossier de sortie personnalisé
set -euo pipefail

DIR="${1:-certs}"
mkdir -p "$DIR"

# Un seul commun dénominateur technique (DNS + IP) sur les neuf noms
# d'hôtes internes, plus localhost/127.0.0.1 pour les smoke tests exécutés
# hors docker-compose (voir la vérification dans le rapport de ce chantier).
SAN="subjectAltName=DNS:auth-service,DNS:abonne-service,DNS:campagne-service,DNS:facturation-service,DNS:paiement-service,DNS:notification-service,DNS:reporting-service,DNS:config-service,DNS:gateway,DNS:localhost,IP:127.0.0.1"

# ── CA interne auto-signée ───────────────────────────────────────────────────
openssl genpkey -algorithm RSA -pkeyopt rsa_keygen_bits:4096 -out "$DIR/ca.key"
openssl req -x509 -new -key "$DIR/ca.key" -sha256 -days 3650 \
  -subj "/O=SGFE/CN=SGFE Internal gRPC CA" \
  -out "$DIR/ca.crt"

# ── Certificat serveur/client partagé, signé par la CA ci-dessus ────────────
openssl genpkey -algorithm RSA -pkeyopt rsa_keygen_bits:2048 -out "$DIR/server.key"
openssl req -new -key "$DIR/server.key" \
  -subj "/O=SGFE/CN=sgfe-grpc-internal" \
  -out "$DIR/server.csr"
openssl x509 -req -in "$DIR/server.csr" -CA "$DIR/ca.crt" -CAkey "$DIR/ca.key" \
  -CAcreateserial -days 1825 -sha256 \
  -extfile <(printf "%s\n" "$SAN") \
  -out "$DIR/server.crt"
rm -f "$DIR/server.csr" "$DIR/ca.srl"

chmod 600 "$DIR/ca.key" "$DIR/server.key"
chmod 644 "$DIR/ca.crt" "$DIR/server.crt"

echo "✅ CA + certificat mTLS générés dans $DIR/ (ca.crt, ca.key, server.crt, server.key) — gitignorés."
echo "   server.crt/server.key sont réutilisés comme identité serveur ET cliente par les neuf composants (voir l'en-tête de ce script)."
echo "   Ne jamais committer ces fichiers."
