# syntax=docker/dockerfile:1
FROM nginx:1.29-alpine@sha256:5616878291a2eed594aee8db4dade5878cf7edcb475e59193904b198d9b830de

# apk upgrade en plus du digest pinné : applique les correctifs de sécurité
# publiés par Alpine depuis la construction de cette image de base, sans
# attendre qu'un nouveau digest amont soit disponible.
RUN apk update && apk upgrade --no-cache && apk add --no-cache curl

COPY default.conf /etc/nginx/conf.d/default.conf

HEALTHCHECK --interval=30s --timeout=3s --start-period=5s --retries=3 \
    CMD curl -fs http://localhost/healthz || exit 1
