# syntax=docker/dockerfile:1
FROM node:18-slim@sha256:f9ab18e354e6855ae56ef2b290dd225c1e51a564f87584b9bd21dd651838830e

# Chromium et ses dépendances pour Puppeteer
RUN apt-get update && apt-get install -y \
    chromium \
    fonts-freefont-ttf \
    --no-install-recommends \
    && rm -rf /var/lib/apt/lists/*

# Puppeteer doit utiliser le Chromium système, pas en télécharger un
ENV PUPPETEER_SKIP_CHROMIUM_DOWNLOAD=true
ENV PUPPETEER_EXECUTABLE_PATH=/usr/bin/chromium

WORKDIR /app

COPY package*.json ./
RUN npm install --omit=dev

COPY server.js .

# Préparer le dossier de session et céder la propriété à l'utilisateur non-root
# (Puppeteer/Chromium a besoin d'écrire sous /app à l'exécution — profil de
# session, caches — donc pas de durcissement en lecture seule ici).
RUN mkdir -p /app/session && chown -R node:node /app

# Basculer vers l'utilisateur non-root pré-existant dans l'image node:18-slim
USER node

# Volume pour persister la session WhatsApp entre les redémarrages
VOLUME ["/app/session"]

EXPOSE 3000

HEALTHCHECK --interval=30s --timeout=3s --start-period=15s --retries=3 \
    CMD node -e "require('http').get('http://localhost:3000/health', res => process.exit(res.statusCode === 200 ? 0 : 1)).on('error', () => process.exit(1))"

CMD ["node", "server.js"]
