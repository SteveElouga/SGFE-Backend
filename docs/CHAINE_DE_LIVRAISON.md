# Chaîne de livraison — du merge sur `main` aux conteneurs qui tournent

> **Nature de ce document :** plan d'implémentation du **déploiement automatisé sur AWS**. Il décrit qui fait quoi entre Ansible, GitHub Actions et Docker, dans quel ordre, et pourquoi. Complète `DEPLOYMENT.md` (qui décrit le lancement manuel) et remplace l'horizon ② d'`AUDIT_SGFE.md` §10 — voir « Ce que ce document supersède ».
> **Document jumeau :** le **dimensionnement de l'instance, les coûts et les choix de services managés** sont traités séparément dans [`INFRASTRUCTURE_AWS.md`](./INFRASTRUCTURE_AWS.md). Ici, on suppose la machine existante et on décrit ce qui s'y déploie.
> **Convention :** chaque étape indique l'acteur qui l'exécute entre crochets `[Acteur]`. Les références `fichier:ligne` renvoient à l'état du dépôt au 28 août 2026.
> **Dernière mise à jour :** 2026-08-28. À maintenir à chaque changement de la chaîne de livraison.

---

## Table des matières

1. [Ce que ce document supersède](#1-ce-que-ce-document-supersède)
2. [Le verrou : la production recompile au lieu de tirer](#2-le-verrou--la-production-recompile-au-lieu-de-tirer)
3. [Acteurs et responsabilités](#3-acteurs-et-responsabilités)
4. [Séquence d'un merge sur `main`](#4-séquence-dun-merge-sur-main)
5. [Décisions structurantes](#5-décisions-structurantes)
6. [Phase 0 — rendre le compose déployable](#6-phase-0--rendre-le-compose-déployable)
7. [Phase 1 — Ansible, l'infrastructure AWS](#7-phase-1--ansible-linfrastructure-aws)
8. [Phase 2 — Ansible, l'amorçage de la machine](#8-phase-2--ansible-lamorçage-de-la-machine)
9. [Phase 3 — GitHub Actions, le déploiement](#9-phase-3--github-actions-le-déploiement)
10. [Phase 4 — aligner le frontend](#10-phase-4--aligner-le-frontend)
11. [Stratégie d'optimisation des pipelines](#11-stratégie-doptimisation-des-pipelines)
12. [Ordre d'exécution](#12-ordre-dexécution)
13. [Risques propres à la livraison](#13-risques-propres-à-la-livraison)

---

## 1. Ce que ce document supersède

Deux documents du dépôt décrivent une cible **Azure** :

| Document | Ce qu'il dit | Statut |
|---|---|---|
| `AUDIT_SGFE.md` §10 | ① Local → ② **VM Azure** → ③ AKS + Ansible | Horizon ② remplacé par AWS |
| `DEPLOYMENT.md` | « ② VM Azure (Docker Compose) », « Azure Key Vault », « Flexible Server + PITR » | Sections cloud à réécrire |

**Décision du 28 août 2026 : la cible est AWS, pas Azure.** Les équivalences :

| Azure (prévu) | AWS (retenu) |
|---|---|
| Azure Key Vault | AWS Secrets Manager |
| Flexible Server + PITR | RDS PostgreSQL + PITR |
| App Gateway / Front Door | CloudFront (+ Let's Encrypt sur l'origine) |
| Identité managée | Profil d'instance EC2 (rôle IAM) |
| — | Rôle OIDC GitHub → AWS (remplace la clé SSH) |

**Terraform a été écarté explicitement.** L'infrastructure est décrite en Ansible (collection `amazon.aws`). La contrepartie et sa parade sont traitées en [§7](#7-phase-1--ansible-linfrastructure-aws).

**Ansible avance d'un horizon.** `AUDIT_SGFE.md` §10 le plaçait en ③ (avec Kubernetes) ; il est utilisé dès ② pour l'infrastructure et l'amorçage machine, **mais pas dans la boucle de livraison** — voir [§3](#3-acteurs-et-responsabilités).

---

## 2. Le verrou : la production recompile au lieu de tirer

**Rien d'autre ne peut commencer avant que ce point soit corrigé.**

### Ce que la CI produit

`.github/workflows/ci.yml` construit, scanne, atteste et signe chaque service, puis pousse sur GHCR sous deux étiquettes :

```
ghcr.io/<org>/sgfe-backend/auth-service:main
ghcr.io/<org>/sgfe-backend/auth-service:<git-sha>      ← immuable
```

Avec, sur les 9 jobs `publish-*` : `sbom: true`, `provenance: true`, scan Trivy, et `cosign sign` en keyless via l'OIDC GitHub (`ci.yml:567`, `ci.yml:570`).

### Ce que la production exécute

`DEPLOYMENT.md:34` prescrit :

```sh
docker compose -f docker-compose.yml -f docker-compose.prod.yml up -d --build
```

Et **les 11 services applicatifs ont `build:` dans `docker-compose.yml`**, pas `image:` :

```
whatsapp-service  auth-service  facturation-service  paiement-service
notification-service  abonne-service  campagne-service  config-service
reporting-service  gateway  nginx
```

### Conséquence

La machine de production **recompile depuis les sources**. L'image scannée par Trivy, signée par cosign et dont le SBOM est publié **n'est pas celle qui tourne**. La chaîne de confiance de la CI s'arrête au registre.

Effets secondaires :

- la machine de production compile au lieu de servir ;
- le code source doit vivre sur la machine de production ;
- deux constructions du même commit peuvent différer, ce qui rend le retour arrière incertain.

### Deux services n'ont aucune image

`config-service` et `reporting-service` ont un job `test-*` mais **ni `docker-build-*`, ni `publish-*`**. Publication réelle : **9 services sur 11**. Ils doivent rejoindre le rang avant que le modèle « on tire tout » puisse tenir.

---

## 3. Acteurs et responsabilités

Six acteurs, trois cadences. Règle qui les sépare : **plus un travail est fréquent, plus l'outil qui l'exécute doit être léger.**

| Acteur | Responsabilité | Cadence | État |
|---|---|---|---|
| **GitHub Actions — CI** (`ci.yml`) | Teste, lint, audite, scanne, construit, atteste (SBOM + provenance), signe (cosign), pousse sur GHCR sous `:<sha>` | à chaque push | ✅ existe |
| **GitHub Actions — CD** (`cd-prod.yml`) | Assume le rôle AWS par OIDC, vérifie les signatures, envoie l'ordre, joue les migrations, attend la santé, teste, revient en arrière | à chaque merge sur `main` | ❌ **à écrire** |
| **Ansible** | Crée les ressources AWS ; amène une EC2 vierge à l'état « prête » | rare, à la main | ❌ à écrire |
| **Docker sur l'EC2** | **Tire les images** depuis GHCR, remplace les conteneurs, applique les healthchecks | sur ordre du CD | ✅ prêt |
| **AWS SSM** | Transporte la commande jusqu'à la machine, sans SSH ni port 22 | à chaque déploiement | ❌ à activer |
| **AWS Secrets Manager** | Détient clés Django, mots de passe PostgreSQL, clé RS256, jetons Brevo et WhatsApp | au boot | ❌ à créer |

### Ce qu'Ansible ne fait jamais

Il ne tire pas d'image, ne déploie pas à chaque merge, **n'intervient pas dans la boucle de livraison**.

Le déploiement frontend existant montre pourquoi (`SGFE-frontend/.github/workflows/cd-staging.yml`) :

```sh
docker compose pull frontend
docker compose up -d --no-deps frontend
curl -sf http://localhost/health
```

Trois commandes. Rejouer un playbook complet pour y arriver, c'est repayer le provisionnement à chaque livraison — des dizaines de vérifications idempotentes qui passent toujours, pour 60 à 90 secondes de latence ajoutée.

Ansible intervient **avant**, pour que `docker compose pull` fonctionne : moteur installé, session GHCR ouverte, compose déposé, volumes montés. Ensuite il sort du chemin.

---

## 4. Séquence d'un merge sur `main`

| # | Acteur | Action |
|---|---|---|
| 1 | `[GH Actions CI]` | Détecte les services touchés (`dorny/paths-filter`) et ne reconstruit qu'eux. Tests, ruff, bandit, pip-audit, couverture. |
| 2 | `[GH Actions CI]` | Construit, scanne (Trivy), génère SBOM + provenance, signe (cosign keyless), pousse sous `:main` et `:<sha>`. |
| 3 | `[GH Actions CD]` | Échange le jeton OIDC contre des identifiants AWS temporaires. **Aucune clé stockée.** |
| 4 | `[GH Actions CD]` | `cosign verify` sur chaque image à déployer. Signature inconnue → arrêt. |
| 5 | `[AWS SSM]` | Porte la commande jusqu'à l'instance étiquetée `Project=SGFE`. Pas de SSH, pas d'IP à connaître. |
| 6 | `[Docker EC2]` | Écrit le nouveau SHA dans `/opt/sgfe/.env`, puis **tire les images** depuis GHCR. |
| 7 | `[GH Actions CD]` | **Joue les migrations dans un conteneur éphémère**, avant tout redémarrage. Échec → arrêt, l'ancienne version tourne toujours. |
| 8 | `[Docker EC2]` | `up -d --no-build` : remplace les conteneurs. Les healthchecks des images démarrent. |
| 9 | `[GH Actions CD]` | Attend que les 21 conteneurs soient `healthy`, puis test de fumée : requête GraphQL authentifiée. |
| 10 | `[GH Actions CD]` | Échec → réécrit l'ancien SHA dans `.env` et relance. **Le retour arrière est une variable, pas une reconstruction.** |

---

## 5. Décisions structurantes

| Décision | Retenu | Écarté, et pourquoi |
|---|---|---|
| **Étiquette d'image** | `:<git-sha>` immuable, injectée par `/opt/sgfe/.env` | `:latest` — pas de retour arrière, pas de traçabilité, et deux merges rapprochés se marchent dessus |
| **Transport de la commande** | AWS SSM `send-command` | SSH — une clé privée permanente en secret GitHub donne un shell complet sur la production |
| **Déclenchement du `pull`** | Poussé par le CD, à chaque merge | Agent tiré (Watchtower, Flux) — un composant de plus, et on ne sait plus *quand* le déploiement a eu lieu |
| **Migrations** | Étape séparée, avant le redémarrage, conteneur éphémère | Au démarrage du conteneur — avec `restart: unless-stopped`, une migration fautive boucle indéfiniment |
| **Portée d'Ansible** | Infrastructure et amorçage machine | La boucle de livraison — trop lourd pour trois commandes |

> **Le choix des bases — RDS ou conteneurs — ne relève pas de ce document.** Il est arbitré dans [`INFRASTRUCTURE_AWS.md`](./INFRASTRUCTURE_AWS.md) §5, sur la base de la consommation mesurée. La chaîne de livraison est identique dans les deux cas : seules changent les huit variables `*_DB_HOST` du fichier `.env` déposé par Ansible.

---

## 6. Phase 0 — rendre le compose déployable

**Acteur : développeur. Une PR. Bloquant pour tout le reste.**

Il s'agit de séparer ce qui **construit** de ce qui **exécute**.

- [ ] `docker-compose.yml` garde ses `build:` — c'est le fichier du développeur, il doit continuer à construire localement.
- [ ] `docker-compose.prod.yml` **ajoute** un `image:` à chaque service, en plus du durcissement qu'il porte déjà. Il ne peut pas *remplacer* le `build:` du compose de base — Compose fusionne les clés, il n'en retire aucune. C'est `--no-build` au déploiement qui interdit la construction :

  ```yaml
  services:
    auth-service:
      <<: *prod
      image: ghcr.io/<org>/sgfe-backend/auth-service:${IMAGE_TAG}
      environment:
        DJANGO_DEBUG: "False"
  ```

- [ ] Ajouter `docker-build-config`, `publish-config`, `docker-build-reporting`, `publish-reporting` dans `ci.yml`, sur le modèle exact des neuf autres.
- [ ] Retirer `--build` du chemin production de `DEPLOYMENT.md` et y documenter `IMAGE_TAG`.

  > ⚠️ **Corrigé le 31 août 2026 — ce plan était incomplet.** Retirer `--build`
  > ne suffit pas : Compose **construit quand l'image nommée est absente**. Un
  > `IMAGE_TAG` erroné aurait donc déclenché une reconstruction silencieuse, soit
  > exactement le défaut qu'on ferme. Le chemin production doit porter
  > `--no-build`, et les variables être déclarées en `${VAR:?message}` pour
  > échouer plutôt qu'interpoler une chaîne vide.
- [ ] Sortir les **33 valeurs de développement en dur** de `docker-compose.yml` (`devpassword`, `django-insecure-placeholder`, `dev-fake-key`, `whatsapp-dev-placeholder…`) vers un `.env` alimenté par Secrets Manager.

### Comment la machine sait quelle version tirer

Un seul fichier, `/opt/sgfe/.env`, contenant une ligne :

```
IMAGE_TAG=a3f9c2e
```

Le compose l'interpole dans les onze `image:`. **Déployer** = réécrire cette ligne et tirer. **Revenir en arrière** = réécrire l'ancienne. Aucune reconstruction dans les deux sens.

---

## 7. Phase 1 — Ansible, l'infrastructure AWS

**Acteur : Ansible, déclenché à la main.**

```
ansible/
├── inventory/
│   └── prod.aws_ec2.yml        inventaire dynamique, filtré sur tag Project=SGFE
├── group_vars/all.yml          région, tailles, noms de bases
├── 01-infra.yml                VPC · sous-réseaux · SG · EC2 · RDS · S3 · IAM
├── 02-bootstrap.yml            machine vierge → prête
├── 99-teardown.yml             state: absent sur toutes les ressources
└── roles/
    ├── docker/                 moteur, plugin compose, session GHCR
    ├── secrets/                Secrets Manager → /opt/sgfe/.env
    ├── volumes/                EBS dédié pour la session WhatsApp
    ├── observabilite/          agent CloudWatch, rotation des logs
    └── compose/                dépose les deux fichiers compose
```

`01-infra.yml` utilise la collection `amazon.aws` : `ec2_vpc_net`, `ec2_vpc_subnet`, `ec2_security_group`, `ec2_instance`, `rds_instance`, `s3_bucket`, `iam_role`.

### Le rôle OIDC — celui qui appartient à la livraison

`01-infra.yml` crée deux rôles. Le **profil d'instance** `sgfe-ec2-role` relève de l'infrastructure : son détail est en [`INFRASTRUCTURE_AWS.md`](./INFRASTRUCTURE_AWS.md) §6. Celui qui concerne ce document est le second.

**`sgfe-github-deploy`** — assumé par la CD via OIDC. Remplace la clé SSH aujourd'hui stockée dans les secrets GitHub des workflows frontend (`cd-staging.yml`, `cd-canary.yml`, `rollback.yml`) : une clé privée permanente, qui ne tourne jamais et donne un shell complet sur la production.

La politique de confiance doit contraindre **le dépôt et la branche** :

```json
"Condition": {
  "StringEquals": {
    "token.actions.githubusercontent.com:aud": "sts.amazonaws.com"
  },
  "StringLike": {
    "token.actions.githubusercontent.com:sub":
      "repo:<org>/SGFE-backend:ref:refs/heads/main"
  }
}
```

> ⚠️ Sans la condition `sub`, **n'importe quel dépôt GitHub peut assumer le rôle**. C'est l'erreur de configuration OIDC la plus fréquente.

### La discipline qui remplace le fichier d'état

Terraform ayant été écarté, il n'y a **pas d'état** : retirer une tâche du playbook ne supprime pas la ressource dans AWS — elle continue de tourner et de consommer les crédits, en silence.

Deux règles compensent :

1. **Écrire `99-teardown.yml` le même jour que `01-infra.yml`**, jamais après.
2. **Étiqueter toutes les ressources** avec `Project: SGFE`, pour auditer d'un seul appel CLI ce qui existe réellement.

---

## 8. Phase 2 — Ansible, l'amorçage de la machine

**Acteur : Ansible.** C'est ici qu'il est irremplaçable — ce savoir n'existe nulle part aujourd'hui.

| Rôle | Ce qu'il pose |
|---|---|
| `docker` | Moteur, plugin compose, utilisateur non-root `sgfe`, session GHCR via un jeton en lecture seule |
| `volumes` | Montage du volume portant `redis_data` — **c'est là que vit la session WhatsApp**, pas dans `/app/session` (voir §13.1) |
| `secrets` | Lit Secrets Manager via le profil d'instance, écrit `/opt/sgfe/.env` en `0600`. **Aucun secret ne transite par Ansible.** |
| `compose` | Dépose les deux fichiers compose et les clés RS256 (`scripts/gen-jwt-keys.sh`), **sans le code source** |
| `observabilite` | Agent CloudWatch, expédition des logs, rotation locale |

Ansible se connecte lui aussi par SSM :

```yaml
ansible_connection: aws_ssm
```

Le port 22 n'est donc **jamais ouvert** — ni pour l'amorçage, ni pour le déploiement, ni pour l'exploitation.

---

## 9. Phase 3 — GitHub Actions, le déploiement

**Acteur : GitHub Actions.** Un fichier, `.github/workflows/cd-prod.yml`.

> ✅ **Écrit le 31 août 2026.** Le fichier existe et est relisible. Il **ne peut
> pas encore s'exécuter** : son job `prealables` échoue tant que
> `secrets.AWS_DEPLOY_ROLE_ARN` et `vars.GHCR_REPO` ne sont pas définis, c'est-à-dire
> tant que la Phase 1 n'a pas créé l'instance et le rôle. Un échec nommé valait
> mieux qu'un déploiement qui casse à mi-course.
>
> Trois écarts avec l'esquisse ci-dessous, tous dans le sens de la robustesse :
>
> — **Les commandes distantes passent par `jq -n --arg`.** L'esquisse générait la
> liste des migrations côté runner, avec une boucle shell dans une chaîne JSON
> dans du YAML : trois niveaux d'échappement pour ce qu'une boucle sur la machine
> fait en une ligne. Chaque niveau était une occasion de casser en silence.
>
> — **Le point 12 est traité dans le même geste.** Les migrations au démarrage
> sont retirées de `docker-compose.prod.yml` : les garder aurait rendu l'étape
> gatée décorative, et le risque de boucle intact.
>
> — **Le retour arrière ne défait pas les migrations**, et le dit. Une migration
> Django n'est pas toujours réversible ; l'inverser à l'aveugle sur des données de
> production est plus dangereux que de laisser un schéma en avance sur le code.
> Conséquence à connaître : **les migrations doivent rester compatibles vers
> l'arrière** — ajouter une colonne, jamais la renommer en une fois.

```yaml
deploy:
  needs: [ci-status, publish-gateway, publish-auth, publish-abonne,
          publish-campagne, publish-facturation, publish-paiement,
          publish-notification, publish-config, publish-reporting,
          publish-nginx, publish-whatsapp]
  if: github.ref == 'refs/heads/main'
  environment: production          # porte d'approbation manuelle si souhaitée
  permissions:
    id-token: write                # OIDC — aucune clé stockée
    contents: read

  steps:
    - uses: aws-actions/configure-aws-credentials@<sha>
      with:
        role-to-assume: arn:aws:iam::<compte>:role/sgfe-github-deploy
        aws-region: eu-west-3

    - name: Vérifier les signatures
      run: |
        for s in gateway auth-service abonne-service …; do
          cosign verify "ghcr.io/${REPO_LC}/$s:${{ github.sha }}" \
            --certificate-identity-regexp '^https://github.com/<org>/SGFE-backend' \
            --certificate-oidc-issuer https://token.actions.githubusercontent.com
        done

    - name: Déployer
      run: |
        aws ssm send-command \
          --targets "Key=tag:Project,Values=SGFE" \
          --document-name AWS-RunShellScript \
          --parameters 'commands=[
            "cd /opt/sgfe",
            "cp .env .env.precedent",
            "echo IMAGE_TAG=${{ github.sha }} > .env",
            "docker compose -f docker-compose.yml -f docker-compose.prod.yml pull",
            "docker compose -f docker-compose.yml -f docker-compose.prod.yml run --rm auth-service python manage.py migrate",
            "docker compose -f docker-compose.yml -f docker-compose.prod.yml up -d --no-build"
          ]'

    - name: Attendre que les conteneurs soient sains
    - name: Test de fumée GraphQL authentifié
    - name: En cas d'échec — restaurer .env.precedent et relancer
```

> **Note.** Les migrations sont montrées ici sur une seule ligne pour la lisibilité. En pratique, **une étape par service** avec arrêt au premier échec — voir [§13](#13-risques-propres-à-la-livraison).

### Trois choses déjà en place

| Élément | État |
|---|---|
| **Healthchecks** | Le compose n'en déclare que 9 (redis + 8 PostgreSQL), mais **les 11 Dockerfiles backend portent un `HEALTHCHECK`** et Docker les applique même sans déclaration. `docker compose ps --format json` donne l'état des 21 conteneurs. |
| **Étiquettes immuables** | `ci.yml:563` pousse déjà `:<sha>`. Le retour arrière est gratuit dès le premier jour. |
| **Signature** | `cosign sign` en keyless existe. Ajouter `cosign verify` côté déploiement referme la boucle, sans nouvelle infrastructure. |

---

## 10. Phase 4 — aligner le frontend

Le dépôt `SGFE-frontend` présente l'inverse du backend : un déploiement sophistiqué (préproduction, canari 5 % → 25 % → 100 % avec portes manuelles, workflow de retour arrière) mais **une CI qui ne teste rien**. Le canari livre progressivement des artefacts jamais vérifiés.

| Point | Backend | Frontend |
|---|---|---|
| Actions épinglées au SHA | 101 / 101 | **0 / 25** |
| Tests unitaires exécutés en CI | 915 | **0** — `ng test` jamais appelé (198 tests dormants) |
| Tests e2e | — | **0** — `playwright test --pass-with-no-tests` sur un `testDir` inexistant |
| Lint · secrets · image · signature | ruff · gitleaks · Trivy · cosign | aucun |

À faire :

- [ ] Brancher `ng test` dans `ci.yml`.
- [ ] Retirer `--pass-with-no-tests`, créer `e2e/specs/`, corriger le `baseURL` (4200 → port réel).
- [ ] Épingler les 25 actions au SHA.
- [ ] Ajouter gitleaks, Trivy et cosign — parité avec le backend.
- [ ] Migrer le SPA vers S3 + CloudFront **en conservant `/graphql` sur la même origine** : le cookie `refresh_token` est en `SameSite=Strict` (voir `SGFE-frontend/CLAUDE.md`, « Proxy de développement »). Le `nginx/conf.d/default.conf` du frontend proxifie aujourd'hui `/graphql` et `/espace-abonne/` ; ces deux routes deviennent des comportements CloudFront vers l'origine EC2.

---

## 11. Stratégie d'optimisation des pipelines

Les deux pipelines ne sont pas au même niveau, et aucun des deux n'est optimal. Cette section les traite ensemble, en distinguant **ce qu'il ne faut pas casser**, **ce qui gaspille**, et **ce qui manque**.

### 11.1 Ce qui est déjà bon — inventaire à préserver

| Pratique | Backend | Frontend |
|---|---|---|
| `concurrency` + `cancel-in-progress` au niveau workflow | ✅ | ✅ |
| `permissions: contents: read` par défaut, élargi par job | ✅ | partiel |
| Builds sélectifs (`dorny/paths-filter`) | ✅ | ✗ |
| Gate d'agrégation unique (`ci-status`) | ✅ | ✗ |
| Cache des dépendances | ✅ 8 (pip, par service) | ✅ 2 (npm) |
| Cache de couches Docker (`type=gha`, scopé par service) | ✅ | ✅ |
| Actions épinglées au SHA | ✅ **101 / 101** | ✗ **0 / 25** |
| Gate de couverture `--fail-under=80` | ✅ | ✗ |
| SBOM + provenance + signature cosign keyless | ✅ | ✗ |
| Dependabot | ✅ 20 entrées | ✗ |
| `CODEOWNERS` | ✗ | ✅ |
| `pre-commit` | ✅ | ✅ |

> Deux dépôts, deux moitiés. Le backend a Dependabot sans `CODEOWNERS` ; le frontend a `CODEOWNERS` sans Dependabot. Chacun possède ce qui manque à l'autre.

### 11.2 Axe 1 — supprimer le double build *(gain immédiat, risque nul)*

Sur un push vers `main` ou `develop`, chaque service modifié est construit **deux fois** :

| Job | Condition | Ce qu'il fait |
|---|---|---|
| `docker-build-<svc>` | `changes.<svc> == true` | construit + scanne (Trivy) |
| `publish-<svc>` | `changes.<svc> == true` **et** push sur `main`/`develop` | construit + pousse + signe |

Jusqu'à **18 jobs de construction là où 9 suffisent**. Le cache `type=gha` amortit une partie du calcul, mais pas le démarrage des runners, le `checkout`, ni la mise en place de buildx.

**Correctif :**

```yaml
docker-build-auth:
  if: needs.changes.outputs.auth == 'true' && github.event_name == 'pull_request'
```

Et **déplacer le scan Trivy dans `publish-*`**. Aujourd'hui l'image scannée n'est pas l'image publiée : elles ont le même contenu, mais formellement le scan ne porte pas sur l'artefact livré. Un seul job qui construit, scanne, signe et pousse referme cet écart.

### 11.3 Axe 2 — matrice et workflow réutilisable *(842 lignes → ≈ 250)*

30 jobs, dont **26 sont du copier-coller** réparti en trois familles :

```
test-*           × 8      docker-build-*   × 9      publish-*   × 9
```

Pour les tests, une matrice remplace les huit copies :

```yaml
test:
  strategy:
    fail-fast: false
    matrix:
      service: [auth, abonne, campagne, facturation, paiement,
                notification, config, reporting, gateway]
  timeout-minutes: 15
  steps:
    - uses: actions/checkout@<sha>
    - uses: actions/setup-python@<sha>
      with:
        python-version: '3.12'
        cache: pip
        cache-dependency-path: services/${{ matrix.service }}/requirements.txt
    - run: ruff check .
    - run: pip-audit
    - run: bandit -r .
    - run: coverage run … && coverage report -m --fail-under=80
```

Pour les images, un workflow réutilisable `.github/workflows/_build-publish.yml` déclaré `on: workflow_call`, appelé une fois par service via une matrice.

> **Le vrai gain n'est pas le nombre de lignes.** Ajouter `config-service` et `reporting-service` à la publication — le blocage de la [phase 0](#6-phase-0--rendre-le-compose-déployable) — devient **deux entrées dans une liste** au lieu de deux copies de 90 lignes qu'il faut relire pour vérifier qu'on n'a rien oublié. C'est précisément parce que ces jobs sont dupliqués que ces deux services ont été oubliés.

### 11.4 Axe 3 — les garde-fous d'exécution absents des deux dépôts

| Garde-fou | Backend | Frontend | Conséquence de l'absence |
|---|---|---|---|
| `timeout-minutes` | **0 / ~33 jobs** | **0 / ~17 jobs** | Défaut GitHub : **360 minutes**. Un test bloqué immobilise un runner six heures. |
| `matrix` | 0 | 0 | duplication (§11.3) |
| `workflow_call` | 0 | 0 | duplication (§11.3) |

**Correctif :** `timeout-minutes: 15` sur les jobs de test, `30` sur les jobs de construction, `10` sur le déploiement. Une ligne par job, et le coût d'un blocage passe de six heures à quinze minutes.

### 11.5 Axe 4 — hisser le frontend au niveau du backend

Ce n'est pas une optimisation, c'est une remise à niveau : plusieurs de ces points sont des **défauts de correction**, pas de performance.

| # | Action | Nature |
|---|---|---|
| 1 | Brancher `ng test` dans `ci.yml` — **198 tests existent et ne s'exécutent nulle part** | correction |
| 2 | Retirer `--pass-with-no-tests` et créer `e2e/specs/` — le job « e2e » est vert sans rien tester | correction |
| 3 | Corriger le `baseURL` Playwright (4200 → port réel) | correction |
| 4 | Épingler les **25 actions** au SHA | sécurité |
| 5 | Ajouter gitleaks, Trivy, `npm audit`, cosign | sécurité |
| 6 | Ajouter un gate d'agrégation `ci-status`, comme le backend | gouvernance |
| 7 | Adopter `dorny/paths-filter` si le dépôt se scinde | performance |
| 8 | Épingler les images de base du `Dockerfile` au SHA — les 19 Dockerfiles backend le sont, le frontend utilise `node:22-alpine` et `nginx:1.27-alpine`, étiquettes mobiles | sécurité |

### 11.6 Axe 5 — symétriser les automatismes de dépôt

| À ajouter | Où | Portée |
|---|---|---|
| `dependabot.yml` | frontend | `npm`, `github-actions`, `docker` |
| `CODEOWNERS` | backend | au minimum `/.github/`, `/services/auth/`, `/gateway/schema/` |
| `pull_request_template.md` | les deux | rappel de la checklist (tests, doc, migration) |
| Groupement Dependabot | backend | 20 entrées → PR groupées par écosystème, pour éviter le bruit hebdomadaire |

### 11.7 Axe 6 — fermer la boucle de confiance

La chaîne SLSA est **construite mais pas consommée** : le backend signe et atteste, et personne ne vérifie.

- [ ] `cosign verify` en préalable au déploiement ([§9](#9-phase-3--github-actions-le-déploiement)) — refuse toute image non signée par le workflow attendu ;
- [ ] attacher le SBOM aux releases GitHub (`release.yml`) plutôt que de le laisser uniquement dans le registre ;
- [ ] activer la protection de branche sur `main` en exigeant **le seul contrôle `ci-status`** — il agrège déjà les 9 jobs requis.

### 11.8 Axe 7 — construire en multi-architecture ✅ **FAIT**

> **Fait le 1er septembre 2026**, en même temps que l'axe 6 — les deux étaient
> indissociables, et cette section le disait déjà.
>
> Deux workflows réutilisables, `_build-scan.yml` et `_publish-image.yml`,
> remplacent 22 corps de jobs quasi identiques : **993 → 576 lignes** dans
> `ci.yml`. La publication construit `linux/amd64,linux/arm64` en un seul build
> et pousse une liste de manifestes. Le blocage du Graviton est levé.
>
> **Approche retenue : A (émulation QEMU)**, comme prévu.
>
> **Trois pièges sur trois, traités — mais pas tous comme prévu :**
>
> | Piège | Traitement réel |
> |---|---|
> | Portée du cache | `scope=<service>-<arch>` sur les jobs de PR ; scope `-multi` distinct pour la publication, qui construit les deux en un seul build |
> | Signature | `cosign sign --recursive` — comme prévu |
> | Analyse d'image | **pas** `trivy --platform`. `aquasecurity/trivy-action` n'expose aucune entrée `platform` (vérifié dans son `action.yaml` au SHA épinglé) : il aurait fallu parier sur une variable d'environnement non documentée. On construit et scanne **une architecture à la fois**, chargée localement — rien à sélectionner, rien à supposer. |
>
> **Un quatrième piège que cette section ne mentionnait pas, et qui aurait cassé
> le déploiement :** Fulcio dérive l'identité du certificat de
> `job_workflow_ref` — le workflow qui **exécute** le job de signature — et non
> du workflow appelant. L'identité signée est donc `_publish-image.yml@…`, et le
> `--certificate-identity-regexp` de `cd-prod.yml`, ancré sur `ci\.yml@`, aurait
> fait échouer la vérification des onze images. Corrigé dans `cd-prod.yml` et
> dans l'exemple de `DEPLOYMENT.md`.

Prérequis au choix d'une instance Graviton, arbitré dans [`INFRASTRUCTURE_AWS.md`](./INFRASTRUCTURE_AWS.md) §2 : ≈ 6 $/mois nets. Ci-dessous, l'analyse qui a conduit à cette mise en œuvre.

**Ce qui existe déjà :** `docker/setup-buildx-action` est présent **18 fois** — buildx est en place. **Ce qui manque :** `docker/setup-qemu-action` (0 occurrence) et `platforms:` (0 occurrence). Les 29 jobs tournent sur `ubuntu-latest`, donc amd64.

#### Trois approches

| | Mise en œuvre | Coût | Verdict |
|---|---|---|---|
| **A — Émulation QEMU** | 2 lignes : `setup-qemu-action` + `platforms: linux/amd64,linux/arm64` | temps de build ×2 à ×3 | **à retenir en premier** |
| **B — Runners natifs** | matrice `[ubuntu-latest, ubuntu-24.04-arm]`, poussée **par digest**, puis fusion via `docker buildx imagetools create` | ≈ 1 $/mois de *larger runners* | si le temps de build devient pénible |
| **C — arm64 seul** | une seule plateforme | 0 | **écarté** — casse les développeurs sur Mac Intel et les tests de fumée de la CI, qui tournent sur amd64 |

> ⚠️ Les runners arm64 **gratuits** de GitHub sont réservés aux **dépôts publics**. SGFE étant privé, l'approche B suppose des *larger runners* arm64, facturés à 0,005 $/min.

L'émulation est ici peu coûteuse parce que **toutes les roues aarch64 existent** (voir le tableau d'`INFRASTRUCTURE_AWS.md` §2) : `pip install` ne compile presque rien, et seuls `apt-get` et le travail de couches sont émulés.

#### Trois pièges

| Piège | Conséquence si ignoré | Correctif |
|---|---|---|
| **Portée du cache** | Les jobs utilisent `cache-from: type=gha,scope=auth-service`. Les deux architectures écriraient dans le même scope et **s'invalideraient mutuellement** à chaque build. | `scope=auth-service-${{ matrix.platform }}` |
| **Signature** | `cosign sign` signe la **liste de manifestes**, pas les images enfants. La vérification ne porterait pas sur la variante arm64 réellement déployée. | `cosign sign --recursive` |
| **Analyse d'image** | Trivy ne scanne **qu'une architecture à la fois**, celle de l'hôte par défaut. On scannerait amd64 et on déploierait arm64. | `trivy --platform linux/arm64` |

#### À faire après l'axe 2

Ajouter `platforms:` à neuf jobs dupliqués, c'est neuf modifications à garder synchronisées ; à un workflow réutilisable, c'en est **une**. Le multi-architecture est un argument de plus pour dédupliquer d'abord.

### 11.9 Ordre de traitement recommandé

| # | Action | Effet | Risque |
|---|---|---|---|
| 1 | `timeout-minutes` partout | plafonne le coût d'un blocage | nul |
| 2 | `docker-build-*` limité aux PR + Trivy dans `publish-*` | −9 jobs par merge, scan aligné sur l'artefact livré | faible |
| 3 | `ng test` branché, `--pass-with-no-tests` retiré | **la CI frontend teste enfin quelque chose** | faible |
| 4 | 25 actions frontend épinglées au SHA | supprime la dérive silencieuse d'action | faible |
| 5 | Matrice sur `test-*` | 842 → ≈ 500 lignes | moyen |
| 6 | Workflow réutilisable ✅ **fait** — deux workflows (`_build-scan.yml`, `_publish-image.yml`) | **−417 lignes** dans `ci.yml` | moyen |
| 7 | `platforms: linux/amd64,linux/arm64` via QEMU ✅ **fait** | **Graviton débloqué** — ≈ 6 $/mois nets | faible |

> `config` et `reporting` avaient été débloqués plus tôt, en #142, sans attendre
> la déduplication : leurs jobs de build, de test et de publication ont été
> ajoutés à la main. La déduplication n'était donc plus un prérequis, seulement
> la bonne façon d'ajouter le multi-architecture sans créer une duplication
> de 22 configurations à garder synchronisées.
| 8 | Dependabot frontend, `CODEOWNERS` backend | symétrie | nul |
| 9 | `cosign verify --recursive` au déploiement | referme la chaîne de confiance | faible |

Les points 1 à 4 se font en une PR chacun, sans toucher à la structure. Les points 5 et 6 sont une refonte à mener d'un bloc, idéalement **avant** la [phase 0](#6-phase-0--rendre-le-compose-déployable) — puisque c'est elle qui exige d'ajouter deux services à la publication.

---

## 12. Ordre d'exécution

| # | Étape | Acteur | Débloque | Effort |
|---|---|---|---|---|
| 0 | `image:` au lieu de `build:` · 2 services publiés · secrets sortis | Dév | tout le reste | 1 j |
| 1 | Secrets Manager rempli, rôles IAM créés | Ansible | l'amorçage et l'OIDC | 1 j |
| 2 | VPC, EC2, RDS, S3, étiquetage | Ansible | la machine cible | 1–2 j |
| 3 | Amorçage : Docker, volumes, compose, CloudWatch | Ansible | le premier `pull` | 1 j |
| 4 | *(optionnel)* bascule des 8 bases vers RDS — arbitrage en `INFRASTRUCTURE_AWS.md` §5 | Dév + Ansible | la durabilité, pas la RAM | 1 j |
| 5 | `cd-prod.yml` : OIDC, SSM, migrations, santé, rollback | GH Actions | **le déploiement au merge** | 2 j |
| 6 | TLS, CloudFront, SPA vers S3 | Ansible + Dév | l'exposition publique | 1 j |
| 7 | Alignement de la CI frontend | Dév | la confiance dans le canari | 2 j |
| 8 | Alarmes CloudWatch, `99-teardown.yml` vérifié | Ansible | l'exploitation | 1 j |

Les étapes 0 à 3 forment le socle et se tiennent. **À partir de la 5, chaque merge sur `main` se déploie seul.** Les étapes 6 à 8 durcissent.

*Efforts estimés, pas mesurés.*

---

## 13. Risques propres à la livraison

> Les risques d'**infrastructure** — mono-zone, session WhatsApp, sauvegardes locales — sont traités dans [`INFRASTRUCTURE_AWS.md`](./INFRASTRUCTURE_AWS.md) §11. Ci-dessous, ceux que la chaîne de livraison crée ou peut éviter.

### 13.1 Un redéploiement peut détruire la session WhatsApp

**Point de vigilance majeur pour tout déploiement automatique.** La session WhatsApp n'est pas stockée là où on l'attend : `whatsapp-service/redis-store.js` implémente un store `RemoteAuth` qui **la conserve dans Redis**, compressée en zip et persistée par l'AOF sur le volume `redis_data`. Le répertoire `/app/session` n'est qu'un espace de travail.

Trois règles en découlent pour le workflow de déploiement :

- **ne jamais inclure `redis` dans un `docker compose down -v`** ni dans une recréation de volume — la session part avec, et il faut un humain, un téléphone et un QR code pour la rétablir ;
- le déploiement ne doit toucher qu'aux services applicatifs (`up -d` remplace les conteneurs dont l'image a changé ; Redis n'en fait pas partie tant que sa version est figée) ;
- une montée de version de l'image `redis` est une **opération à part**, planifiée, avec snapshot préalable du volume.

### 13.2 Migration automatique au démarrage = boucle de redémarrage

Chaque service Django lance `python manage.py migrate` à son démarrage (`command:` dans `docker-compose.yml`, documenté dans `DEPLOYMENT.md` §Migrations). Confortable en développement.

En production avec `restart: unless-stopped` (`docker-compose.prod.yml:16`), une migration qui échoue fait redémarrer le conteneur, qui rejoue la migration, qui échoue encore — **indéfiniment, service indisponible**.

**Parade :** sortir `migrate` du `command:` et en faire une étape distincte du workflow de déploiement, avant le `up -d` (étape 7 de la [séquence](#4-séquence-dun-merge-sur-main)).

### 13.3 Les sauvegardes meurent avec l'instance

Le service `db-backup` écrit ses `pg_dump` dans `./backups/` — **sur le disque de la machine qu'ils sont censés protéger**. Une perte d'instance emporte la base *et* ses sauvegardes.

C'est le seul point de ce document où la panne est **totale et irréversible**.

**Parade :** expédition vers `s3://sgfe-backups` (versionnement + cycle de vie), snapshots EBS quotidiens, même chemin pour `facturation_pdfs`. RDS avec PITR est l'option supérieure mais payante — arbitrage en [`INFRASTRUCTURE_AWS.md`](./INFRASTRUCTURE_AWS.md) §5.

> Les dumps sont désormais chiffrés (AES-256-CBC/PBKDF2, passphrase
> `BACKUP_ENCRYPTION_KEY` — voir `DEPLOYMENT.md` §Sauvegardes et
> `scripts/backup-databases.sh`). Ça répond à un risque différent —
> confidentialité d'un dump exfiltré ou d'un volume mal détruit — et ne change
> rien à la parade ci-dessus : chiffré ou non, le dump reste sur le même disque
> que la base, donc toujours perdu avec elle. L'expédition hors machine reste à
> faire.

---

## Annexe — ce qui a été mesuré

Relevé dans les deux dépôts le **28 août 2026** :

- 21 services dans le compose de production ; 11 en `build:`, 10 en `image:` ;
- 9 jobs `publish-*` (`abonne` `auth` `campagne` `facturation` `gateway` `nginx` `notification` `paiement` `whatsapp`) ; **absents : `config`, `reporting`** ;
- `sbom: true` et `provenance: true` sur les 9 jobs de publication ;
- 101/101 actions épinglées au SHA côté backend, 0/25 côté frontend ;
- 915 fonctions de test backend sur 65 fichiers ; 198 tests frontend sur 24 fichiers ;
- 11/11 Dockerfiles backend portant un `HEALTHCHECK` ; 9 healthchecks déclarés dans le compose ;
- 33 occurrences de valeurs de développement en dur dans `docker-compose.yml` ;
- aucun fichier d'infrastructure (`.tf`, playbook, `ansible.cfg`) dans l'un ou l'autre dépôt ;
- aucun workflow de déploiement backend (`ci.yml` et `release.yml` seulement).

**Estimé, non mesuré :** les efforts en jours du [§12](#12-ordre-dexécution), la consommation mémoire par conteneur, et les coûts mensuels AWS.
