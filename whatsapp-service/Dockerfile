# syntax=docker/dockerfile:1
FROM node:22-slim@sha256:813a7480f28fdadac1f7f5c824bcdad435b5bc1322a5968bbbdef8d058f9dff4

# apt-get upgrade en plus du digest pinné : applique les correctifs de
# sécurité publiés par Debian depuis la construction de cette image de
# base, sans attendre qu'un nouveau digest amont soit disponible.
# Chromium et ses dépendances pour Puppeteer.
RUN apt-get update && apt-get upgrade -y && apt-get install -y \
    chromium \
    fonts-freefont-ttf \
    --no-install-recommends \
    && rm -rf /var/lib/apt/lists/*

# Puppeteer doit utiliser le Chromium système, pas en télécharger un
ENV PUPPETEER_SKIP_CHROMIUM_DOWNLOAD=true
ENV PUPPETEER_EXECUTABLE_PATH=/usr/bin/chromium

WORKDIR /app

COPY package*.json ./
RUN npm install --omit=dev \
    # npm/npx/corepack ne servent qu'à l'installation ; les retirer réduit
    # la surface d'attaque (dépendances tierces embarquées par npm lui-même,
    # ex. picomatch) sans affecter l'exécution (CMD n'appelle que "node").
    && rm -rf /usr/local/lib/node_modules/npm /usr/local/bin/npm /usr/local/bin/npx /usr/local/bin/corepack

COPY server.js .

# Préparer le dossier de session et céder la propriété à l'utilisateur non-root
# (Puppeteer/Chromium a besoin d'écrire sous /app à l'exécution — profil de
# session, caches — donc pas de durcissement en lecture seule ici).
RUN mkdir -p /app/session && chown -R node:node /app

# Basculer vers l'utilisateur non-root pré-existant dans l'image node:22-slim
USER node

# Volume pour persister la session WhatsApp entre les redémarrages
VOLUME ["/app/session"]

EXPOSE 3000

HEALTHCHECK --interval=30s --timeout=3s --start-period=15s --retries=3 \
    CMD node -e "require('http').get('http://localhost:3000/health', res => process.exit(res.statusCode === 200 ? 0 : 1)).on('error', () => process.exit(1))"

CMD ["node", "server.js"]
