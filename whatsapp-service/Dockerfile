FROM node:18-slim

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
RUN mkdir -p /app/session && chown -R node:node /app

# Basculer vers l'utilisateur non-root pré-existant dans l'image node:18-slim
USER node

# Volume pour persister la session WhatsApp entre les redémarrages
VOLUME ["/app/session"]

EXPOSE 3000

CMD ["node", "server.js"]
