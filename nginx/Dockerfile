# syntax=docker/dockerfile:1
FROM nginx:1.29-alpine@sha256:5616878291a2eed594aee8db4dade5878cf7edcb475e59193904b198d9b830de

# apk upgrade en plus du digest pinné : applique les correctifs de sécurité
# publiés par Alpine depuis la construction de cette image de base, sans
# attendre qu'un nouveau digest amont soit disponible.
RUN apk update && apk upgrade --no-cache && apk add --no-cache curl

# Points de montage attendus par default.conf : certificat TLS
# (fullchain.pem/privkey.pem — auto-signé en dev, Let's Encrypt en prod) et
# webroot du défi HTTP-01 de certbot. Créés ici pour que `nginx -t` et un
# démarrage sans montage explicite (ex. test local de l'image seule) ne
# plantent pas sur un chemin inexistant ; en usage réel, docker-compose
# monte par-dessus (voir docker-compose.yml / docker-compose.prod.yml).
RUN mkdir -p /etc/nginx/certs /var/www/certbot

COPY default.conf /etc/nginx/conf.d/default.conf

HEALTHCHECK --interval=30s --timeout=3s --start-period=5s --retries=3 \
    CMD curl -fs http://localhost/healthz || exit 1
