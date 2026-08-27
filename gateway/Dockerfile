# syntax=docker/dockerfile:1
FROM python:3.12-slim@sha256:7a8b475003c4fe15a2cd4e55e5cfc2f3560bdc9333d624f24cdd6d4340fd7a17 AS builder

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1

WORKDIR /app

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
COPY gateway/ gateway/
COPY schema/ schema/
COPY proto/ proto/

USER appuser

EXPOSE 8000

HEALTHCHECK --interval=30s --timeout=3s --start-period=10s --retries=3 \
    CMD python -c "import urllib.request; urllib.request.urlopen('http://localhost:8000/graphql', timeout=2)" || exit 1

CMD ["daphne", "-b", "0.0.0.0", "-p", "8000", "gateway.asgi:application"]
