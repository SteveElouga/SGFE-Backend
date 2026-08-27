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

RUN useradd --create-home --uid 1000 appuser

# Pas de --chown : le code et les dépendances restent root:root, lisibles
# par appuser mais non modifiables — un appuser compromis à l'exécution ne
# peut pas altérer les paquets installés ni le code applicatif.
COPY --from=builder /root/.local /home/appuser/.local
COPY manage.py .
COPY notification/ notification/
COPY notifications/ notifications/
COPY proto/ proto/

USER appuser

EXPOSE 50056

HEALTHCHECK --interval=30s --timeout=3s --start-period=10s --retries=3 \
    CMD python -c "import socket; socket.create_connection(('localhost', 50056), timeout=2)" || exit 1

CMD ["python", "manage.py", "grpc_server"]
