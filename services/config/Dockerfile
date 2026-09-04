# syntax=docker/dockerfile:1
FROM python:3.12-slim@sha256:7a8b475003c4fe15a2cd4e55e5cfc2f3560bdc9333d624f24cdd6d4340fd7a17 AS builder

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1

WORKDIR /app

RUN apt-get update \
    && apt-get install -y --no-install-recommends gcc libpq-dev \
    && rm -rf /var/lib/apt/lists/*

COPY requirements.txt .
RUN pip install --no-cache-dir --default-timeout=120 --retries 5 --user -r requirements.txt

FROM python:3.12-slim@sha256:7a8b475003c4fe15a2cd4e55e5cfc2f3560bdc9333d624f24cdd6d4340fd7a17

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PATH=/home/appuser/.local/bin:$PATH

WORKDIR /app

# Les correctifs de sécurité Debian sortent après la publication de l'image de
# base : l'épinglage par digest, bon pour la reproductibilité, fige aussi les
# CVE système. On applique donc les mises à jour disponibles au moment du build.
# C'est ce qui ferme CVE-2026-14456 (OpenSSL, déni de service) — le digest le
# plus récent de python:3.12-slim portait encore libssl3t64 3.5.6 quand Debian
# proposait déjà 3.5.7.
RUN apt-get update \
    && apt-get upgrade -y \
    && rm -rf /var/lib/apt/lists/*

RUN useradd --create-home --uid 1000 appuser

# Logs applicatifs (rétention/horodatage, voir settings.py § LOGGING) :
# WORKDIR /app reste root:root (voir commentaire ci-dessus), donc appuser
# ne peut pas y créer de sous-dossier lui-même — sans ce dossier pré-créé,
# LOG_DIR.mkdir() plante au démarrage (PermissionError), constaté en
# conditions réelles (jamais sous manage.py test, où TESTING désactive ce
# bloc).
RUN mkdir -p /app/logs && chown appuser:appuser /app/logs

# Pas de --chown : le code et les dépendances restent root:root, lisibles
# par appuser mais non modifiables — un appuser compromis à l'exécution ne
# peut pas altérer les paquets installés ni le code applicatif.
COPY --from=builder /root/.local /home/appuser/.local
COPY manage.py .
COPY config/ config/
COPY parametres/ parametres/
COPY proto/ proto/

USER appuser

EXPOSE 50058

HEALTHCHECK --interval=30s --timeout=3s --start-period=10s --retries=3 \
    CMD python -c "import socket; socket.create_connection(('localhost', 50058), timeout=2)" || exit 1

CMD ["python", "manage.py", "grpc_server"]
