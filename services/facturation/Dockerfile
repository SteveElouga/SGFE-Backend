# syntax=docker/dockerfile:1
FROM python:3.12-slim@sha256:7a8b475003c4fe15a2cd4e55e5cfc2f3560bdc9333d624f24cdd6d4340fd7a17 AS builder

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1

WORKDIR /app

RUN apt-get update \
    && apt-get install -y --no-install-recommends gcc libpq-dev libffi-dev \
    && rm -rf /var/lib/apt/lists/*

COPY requirements.txt .
RUN pip install --no-cache-dir --default-timeout=120 --retries 5 --user -r requirements.txt

FROM python:3.12-slim@sha256:7a8b475003c4fe15a2cd4e55e5cfc2f3560bdc9333d624f24cdd6d4340fd7a17

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PATH=/home/appuser/.local/bin:$PATH

WORKDIR /app

# Bibliothèques natives requises par WeasyPrint à l'exécution (Pango/Cairo/
# GDK-PixBuf) + polices pour que le texte du PDF soit rendu (Liberation Sans
# donne un rendu proche de la maquette, DejaVu couvre les glyphes manquants).
# `apt-get upgrade` est placé APRÈS l'install : plusieurs de ces paquets tirent
# des dépendances transitives (ex. libtiff6 via libgdk-pixbuf) qui, upgradées
# avant leur installation, resteraient à la version vulnérable de l'image de
# base — l'upgrade final applique donc les correctifs de sécurité à TOUT ce qui
# est installé (corrige notamment CVE-2026-12912 sur libtiff6).
RUN apt-get update && apt-get install -y --no-install-recommends \
    libpango-1.0-0 \
    libpangoft2-1.0-0 \
    libpangocairo-1.0-0 \
    libcairo2 \
    libgdk-pixbuf-2.0-0 \
    libffi8 \
    fonts-dejavu-core \
    fonts-liberation2 \
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
# peut pas altérer les paquets installés ni le code applicatif. Exception :
# /app/pdfs, seul répertoire dans lequel ce service écrit réellement
# (génération de PDF, PDF_STORAGE_DIR) et qui reçoit un volume monté.
COPY --from=builder /root/.local /home/appuser/.local
COPY manage.py .
COPY facturation/ facturation/
COPY factures/ factures/
COPY proto/ proto/

RUN mkdir -p /app/pdfs && chown appuser:appuser /app/pdfs

USER appuser

EXPOSE 50054

HEALTHCHECK --interval=30s --timeout=3s --start-period=10s --retries=3 \
    CMD python -c "import socket; socket.create_connection(('localhost', 50054), timeout=2)" || exit 1

CMD ["python", "manage.py", "grpc_server"]
