# syntax=docker/dockerfile:1
FROM python:3.12-slim@sha256:423ed6ab25b1921a477529254bfeeabf5855151dc2c3141699a1bfc852199fbf AS builder

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1

WORKDIR /app

RUN apt-get update \
    && apt-get install -y --no-install-recommends gcc libpq-dev \
    && rm -rf /var/lib/apt/lists/*

COPY requirements.txt .
RUN pip install --no-cache-dir --default-timeout=120 --retries 5 --user -r requirements.txt

FROM python:3.12-slim@sha256:423ed6ab25b1921a477529254bfeeabf5855151dc2c3141699a1bfc852199fbf

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PATH=/home/appuser/.local/bin:$PATH

WORKDIR /app

RUN useradd --create-home --uid 1000 appuser

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
