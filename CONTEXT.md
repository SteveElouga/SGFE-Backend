# CONTEXT.md — Contexte technique & métier (SGFE)

> Document d'onboarding : ce qu'est le projet, comment il est construit, et les règles à connaître. Pour les **règles impératives de collaboration** (Git, sécurité), voir **`MEMORY.md`**. Pour l'audit détaillé et le plan d'action, voir **`AUDIT_SGFE.md`**.

## 1. Le projet

**SGFE — Système de Gestion de Facturation d'Eau.** Digitalise la création et l'envoi des factures d'eau, de bout en bout :

```
Abonnés & compteurs → Campagnes de relevé → Saisie d'index (agents terrain)
→ Facturation (PDF, tarif au m³) → Notification WhatsApp → Paiement (espèces / Mobile Money / virement)
→ Impayés & relances → Suspension/réactivation → Reporting / tableau de bord
```

## 2. Architecture

**Microservices** : chaque service est un projet **Django indépendant** avec **sa propre base PostgreSQL**. Un service ne parle **jamais** directement à la base d'un autre — **toute** communication inter-services passe par **gRPC** (Protocol Buffers). Le frontend ne connaît pas les services : il parle **uniquement** à une **API Gateway GraphQL** (`/graphql`).

```
Angular 22 (PWA)  ──GraphQL──▶  Gateway (Strawberry)  ──gRPC──▶  8 microservices Django
                                                                   │
                                          Redis (pub/sub, streams) ┘   WhatsApp service (Node.js)
```

## 3. Composants

| # | Composant | Rôle | BD |
|---|---|---|---|
| 0 | **Gateway** | GraphQL → gRPC, authz (RBAC), endpoints PDF/CSV | ❌ |
| 1 | **auth** | Utilisateurs, rôles, JWT, activation, OTP | auth_db |
| 2 | **abonne** | Abonnés + compteurs (cycle de vie, remplacement) | abonne_db |
| 3 | **campagne** | Campagnes + relevés + affectation agents/zones | campagne_db |
| 4 | **facturation** | Factures + PDF (WeasyPrint) + tarifs | facturation_db |
| 5 | **paiement** | Paiements + soldes + impayés/relances | paiement_db |
| 6 | **notification** | WhatsApp (tokens espace abonné) + e-mail | notification_db |
| 7 | **reporting** | Read-model CQRS (dashboard, stats) | reporting_db |
| 8 | **config** | Paramètres système + infos société | config_db |
| — | **whatsapp-service** | Passerelle WhatsApp (whatsapp-web.js, Node.js) | Redis |

## 4. Stack

- **Backend :** Django 5.x, gRPC (`grpcio`), GraphQL **Strawberry** (gateway), PostgreSQL 16, **WeasyPrint** (PDF), APScheduler (cron), Redis (pub/sub + streams).
- **Frontend :** **Angular 22** (zoneless, standalone, signals), **PrimeNG**, **Apollo** (GraphQL), `@ngx-translate` (i18n fr/en), PWA. Tests : **Vitest** + **Playwright**.
- **WhatsApp :** Node.js + `whatsapp-web.js` (session persistée en Redis).
- **CI/CD :** GitHub Actions (lint `ruff`, `bandit`, `pip-audit`, gitleaks, gate de couverture 80 %, Trivy, SBOM + cosign) ; Dependabot ; pre-commit.

## 5. Rôles & permissions

Quatre rôles : **ADMIN** (accès total), **AGENT** (saisie terrain, sa tournée), **COMPTABLE** (factures, paiements, dashboard), **SUPERVISEUR** (ses propres campagnes uniquement — filtrage par `created_by`). Le RBAC est appliqué à la **gateway** (`require_role`).

## 6. Règles métier clés

```
consommation   = nouveau_index - ancien_index         # toujours >= 0
montant        = consommation * prix_m3                # prix_m3 COPIÉ dans la facture (jamais de FK)
solde_restant  = montant_total - Σ versements
statut facture = IMPAYEE | PARTIELLE | PAYEE
```

- **Argent en `Decimal`** partout (jamais de `float`), arrondi `ROUND_HALF_UP`.
- Validations : index ≥ ancien ; abonné **ACTIF** avant relevé ; **référence obligatoire** pour Mobile Money / Virement ; anti-surpaiement ; verrous `SELECT … FOR UPDATE` sur soldes et numérotation.
- Escalade impayés en 4 étapes (J+0/3/7/10) → suspension auto → **réactivation + WhatsApp** au paiement du solde.

## 7. Horizons de déploiement

- **① Local (maintenant)** — Docker Compose en local.
- **② Azure (moyen terme)** — migration cloud (Key Vault, PostgreSQL Flexible Server, Azure Cache for Redis, Application Insights, App Gateway/WAF).
- **③ Kubernetes / AKS + Ansible (cible)** — CSI + Workload Identity, Managed Prometheus/Grafana, mesh mTLS.

## 8. Conventions de code

- Commentaires en **français**, code (noms) en **anglais**.
- **Type hints** obligatoires côté backend (jamais de `Any`) ; docstrings sur les fonctions publiques.
- Couverture de tests **> 80 %** backend (imposée en CI). Frontend : cible en montée (parcours critiques d'abord).
- Migrations : une par modification de modèle.
- **Frontend ↔ Gateway :** toujours en **chemin relatif `/graphql`** (same-origin ; cookie refresh `SameSite=Strict`).

## 9. Collaboration & sécurité

Le **workflow Git** (branches, MR, rebase) et les **règles de sécurité** (dont **ne jamais lire `.env`**) sont décrits dans **`MEMORY.md` §1** — **impératifs et non négociables**.

## 10. Documentation

- `AUDIT_SGFE.md` — audit complet, checklist priorisée (94 tâches), plan cadré (3 horizons).
- `MEMORY.md` — règles impératives + décisions + état + prochaines étapes.
- `CLAUDE.md` — conventions détaillées.
- `docs/` — `SRS.md`, `ARCHITECTURE.md` (C4/Arc42), `ADR.md`, `ETAT_DU_SYSTEME.md`, `WORKFLOWS.md`, `DOCUMENTATION_TECHNIQUE.md`.
