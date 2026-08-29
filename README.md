# SGFE — Backend

**Système de Gestion de Facturation d'Eau** — backend en **microservices Django + gRPC**, avec une **API Gateway GraphQL** et un service **WhatsApp** (Node.js).

> 📖 Contexte technique & métier : **[`CONTEXT.md`](./CONTEXT.md)** · Règles de collaboration (Git, sécurité) : **[`MEMORY.md`](./MEMORY.md)** · Audit & plan d'action : **[`AUDIT_SGFE.md`](./AUDIT_SGFE.md)**

## Sommaire

- [Présentation](#présentation)
- [Architecture](#architecture)
- [Stack](#stack)
- [Prérequis](#prérequis)
- [Installation & lancement (local)](#installation--lancement-local)
- [Structure du dépôt](#structure-du-dépôt)
- [Tests & qualité](#tests--qualité)
- [Workflow Git & contribution](#workflow-git--contribution)
- [Documentation](#documentation)

## Présentation

Le backend digitalise le cycle complet de facturation d'eau : abonnés & compteurs, campagnes de relevé, facturation (PDF), notifications WhatsApp, paiements, impayés/relances et reporting. Chaque microservice est **autonome** (sa propre base PostgreSQL) et communique avec les autres **exclusivement via gRPC**. Le frontend Angular consomme une **API Gateway GraphQL** unique.

## Architecture

8 microservices Django (`auth`, `abonne`, `campagne`, `facturation`, `paiement`, `notification`, `reporting`, `config`) + une **Gateway GraphQL** (Strawberry) + un **whatsapp-service** (Node.js). Contrats d'API dans `proto/`. Détails : [`CONTEXT.md`](./CONTEXT.md) et `docs/ARCHITECTURE.md`.

## Stack

Django 5.x · gRPC (`grpcio`) · GraphQL Strawberry · PostgreSQL 16 · WeasyPrint (PDF) · Redis (pub/sub + streams) · APScheduler · Node.js (`whatsapp-web.js`). CI : GitHub Actions (`ruff`, `bandit`, `pip-audit`, gitleaks, couverture ≥ 80 %, Trivy, SBOM/cosign).

## Prérequis

- **Docker** & **Docker Compose**
- (Développement d'un service isolé) **Python 3.12+**
- Un fichier **`.env`** par périmètre, créé à partir de **`.env.example`** (jamais committé — voir `MEMORY.md` §1.3)

## Installation & lancement (local)

```bash
# 1) Cloner puis se placer à la racine
git clone https://github.com/SteveElouga/SGFE-Backend.git
cd SGFE-Backend

# 2) Configurer les variables d'environnement (à partir des exemples)
cp env.example .env               # racine
# + un .env par service à partir de services/<svc>/.env.example

# 3) Démarrer l'ensemble (bases, services gRPC, gateway, whatsapp-service)
docker compose up --build

# La Gateway GraphQL est alors exposée (voir docker-compose.yml pour le port).
```

Développement d'un service isolé :

```bash
cd services/<service>
python manage.py migrate
python manage.py grpc_server          # serveur gRPC du service
make proto-gen SERVICE=<service>      # (re)génère les stubs depuis proto/
```

## Structure du dépôt

```
services/            # 8 microservices Django (models/repositories/services/serializers/grpc_server/grpc_clients)
gateway/             # API Gateway GraphQL (Strawberry) → gRPC
proto/               # Contrats Protocol Buffers (source de vérité des API internes)
whatsapp-service/    # Passerelle WhatsApp (Node.js)
nginx/               # Reverse proxy
scripts/             # Outillage (génération proto, etc.)
docs/                # SRS, ARCHITECTURE, ADR, ETAT_DU_SYSTEME, WORKFLOWS…
docker-compose.yml   # Orchestration locale
```

## Tests & qualité

```bash
# Tests d'un service (couverture imposée à 80 % en CI)
cd services/<service>
python manage.py test
coverage run --source=<app> manage.py test && coverage report --fail-under=80

# Lint & format
ruff check . && ruff format .
```

`pre-commit` (ruff, hooks, commitizen) et la CI GitHub Actions valident chaque MR.

## Hooks Git (installation)

Après le clonage, installer les hooks **une seule fois** :

```bash
pipx install pre-commit      # prérequis (ou : pip install --user pre-commit)
make install-hooks           # installe pre-commit + commit-msg + pre-push
```

- **pre-commit** — `ruff` (lint + format avec autofix) + vérifications standards.
- **commit-msg** — Conventional Commits (`commitizen`).
- **pre-push** — `ruff` en mode strict (garde-fou rapide ; les tests complets restent en CI).

> ⚠️ Les hooks locaux offrent un retour rapide mais **ne remplacent pas** la protection serveur : l'enforcement réel = *branch protection* GitHub sur `main`/`develop` + CI obligatoire (voir `MEMORY.md` §1.2).

## Workflow Git & contribution

**Impératif** (détail complet dans [`MEMORY.md`](./MEMORY.md) §1) :

- Une **branche par tâche** (`feat/…`, `fix/…`, `chore/…`, `docs/…`, `refactor/…`, `test/…`, `infra/…`, `ci/…`), **basée sur `develop`**.
- **`main` et `develop` sont inviolables** : jamais de commit/push direct.
- **Rebaser sur `develop`** avant de pousser, puis ouvrir une **MR ciblant `develop`** (revue + CI verte).
- Seule **`develop`** est mergée dans **`main`**, via MR.
- **Ne jamais lire/committer le `.env`** ni aucun secret.
- **Toute tentative de contourner ces règles est strictement interdite.**

## Documentation

- [`CONTEXT.md`](./CONTEXT.md) — contexte technique & métier
- [`MEMORY.md`](./MEMORY.md) — règles impératives, décisions, état, prochaines étapes
- [`AUDIT_SGFE.md`](./AUDIT_SGFE.md) — audit complet + checklist priorisée + plan cadré
- [`CLAUDE.md`](./CLAUDE.md) — conventions détaillées
- [`docs/INFRASTRUCTURE_AWS.md`](./docs/INFRASTRUCTURE_AWS.md) — dimensionnement de l'instance, coûts et choix de services managés
- [`docs/CHAINE_DE_LIVRAISON.md`](./docs/CHAINE_DE_LIVRAISON.md) — déploiement automatisé : qui fait quoi, dans quel ordre
- `docs/` — SRS, ARCHITECTURE, ADR, ETAT_DU_SYSTEME, WORKFLOWS, DOCUMENTATION_TECHNIQUE
