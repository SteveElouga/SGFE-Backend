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

# Sonde : un POST GraphQL minimal, pas un GET.
#
# Le GET précédent s'appuyait sur la page GraphiQL, qui répondait 200. Depuis
# que GraphiQL et l'introspection sont coupés hors développement, `GET
# /graphql` renvoie 400 — et la sonde échouait en permanence, laissant le
# conteneur `unhealthy` sans que rien ne soit cassé.
#
# `{__typename}` est un méta-champ toujours disponible : il ne demande ni
# authentification ni introspection, et prouve que la couche GraphQL répond,
# pas seulement que le port est ouvert.
HEALTHCHECK --interval=30s --timeout=3s --start-period=10s --retries=3 \
    CMD python -c "import json,urllib.request; r=urllib.request.urlopen(urllib.request.Request('http://localhost:8000/graphql', data=json.dumps({'query':'{__typename}'}).encode(), headers={'Content-Type':'application/json'}), timeout=2); exit(0 if r.status==200 else 1)" || exit 1

CMD ["daphne", "-b", "0.0.0.0", "-p", "8000", "gateway.asgi:application"]
