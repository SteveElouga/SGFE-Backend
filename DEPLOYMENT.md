# Déploiement — SGFE Backend

> ⚠️ **L'horizon ② a changé de cible le 28 août 2026 : AWS, plus Azure.** Ce guide décrit le **lancement manuel**, qui reste valable en local. Pour le déploiement automatisé sur AWS — qui fait quoi entre Ansible, GitHub Actions et Docker — voir **[`docs/CHAINE_DE_LIVRAISON.md`](./docs/CHAINE_DE_LIVRAISON.md)**.
>
> Les mentions « Azure », « Key Vault » et « Flexible Server » ci-dessous sont **périmées** ; leurs équivalents AWS sont donnés en §1 du nouveau document. La commande de production ci-dessous utilise encore `--build` : c'est précisément le verrou que `CHAINE_DE_LIVRAISON.md` §2 demande de lever.

Trois horizons (voir `AUDIT_SGFE.md` §10) : **① local** (Docker Compose) · **② VM cloud** (Docker Compose) · **③ Kubernetes** (plus tard). Ce guide couvre ① et ②.

## Prérequis

1. **Clés JWT RS256** (auth-service) — à générer une fois :

   ```sh
   ./scripts/gen-jwt-keys.sh
   ```

   Les clés (`services/auth/keys/*.pem`, gitignorées) sont montées en lecture seule dans le conteneur auth. **Sans elles, l'auth-service refuse de démarrer** (fail-fast RS256).

2. **Secrets** — ⚠️ le `docker-compose.yml` de base contient des valeurs **DEV en dur** (placeholder `DJANGO_SECRET_KEY`, mots de passe `devpassword`, clé whatsapp placeholder). Elles **ne conviennent pas en production**. Fournir de vrais secrets :

   - `DJANGO_SECRET_KEY` — un par service : `python3 -c "import secrets; print(secrets.token_urlsafe(64))"`
   - mots de passe PostgreSQL (`POSTGRES_PASSWORD` / `<SVC>_DB_PASSWORD`)
   - `WHATSAPP_INTERNAL_API_KEY`, `BREVO_API_KEY`

   Aujourd'hui : remplacer les valeurs dans le compose / via un `.env` de prod. À terme (Azure) : **Azure Key Vault** — c'est l'item P0 « externaliser les secrets ».

## Lancement

- **Local (dev)** :

  ```sh
  docker compose up -d --build
  ```

- **Production** — on **tire** les images publiées, on ne construit pas :

  ```sh
  # /opt/sgfe/.env porte les deux variables (voir ci-dessous)
  docker compose -f docker-compose.yml -f docker-compose.prod.yml pull
  docker compose -f docker-compose.yml -f docker-compose.prod.yml up -d --no-build
  ```

  La surcouche `docker-compose.prod.yml` fait deux choses : elle pointe les onze
  services sur les images que la CI a publiées, scannées (Trivy), signées
  (cosign) et accompagnées de leur SBOM ; et elle durcit le runtime —
  `restart: unless-stopped`, limites mémoire, `DEBUG=False`.

### `--no-build` n'est pas décoratif

Le compose de base porte des `build:`, et Compose **construit localement** quand
l'image nommée est absente. Sans ce drapeau, un `IMAGE_TAG` erroné ne provoquerait
pas d'échec : il déclencherait une reconstruction silencieuse.

C'était le défaut central de cette chaîne — la commande de production portait
`--build`, donc la machine recompilait et **n'exécutait jamais** les images
signées. Toute la chaîne de confiance existait sans être consommée.

### Les deux variables de `/opt/sgfe/.env`

```
GHCR_REPO=steveelouga/sgfe-backend   # le dépôt, en minuscules
IMAGE_TAG=a3f9c2e                    # le SHA de commit à exécuter
```

La syntaxe `${VAR:?message}` du compose fait échouer le déploiement si l'une
manque, plutôt que d'interpoler une chaîne vide et de tirer une référence qui
résoudrait vers `latest` — c'est-à-dire n'importe quoi.

**Déployer** = réécrire `IMAGE_TAG`, tirer, relancer.
**Revenir en arrière** = réécrire l'ancien tag. Aucune reconstruction dans les
deux sens, et le retour arrière est aussi rapide que l'aller.

### Vérifier une signature avant de déployer

La chaîne cosign n'a d'intérêt que si quelqu'un la consomme :

```sh
cosign verify \
  --certificate-identity-regexp "https://github.com/${GHCR_REPO}/.github/workflows/ci.yml@.*" \
  --certificate-oidc-issuer https://token.actions.githubusercontent.com \
  "ghcr.io/${GHCR_REPO}/auth-service:${IMAGE_TAG}"
```

## Migrations

**Automatiques** : chaque service Django lance `python manage.py migrate` à son démarrage (`command:` dans le compose). Aucune étape manuelle.

## Réseau & point d'entrée

Seul **nginx** est exposé (`:8080` → gateway). Les ports internes (gRPC `50051-50058`, PostgreSQL `5432-5439`, Redis) **ne sont pas publiés** (isolation réseau). Un rate limiting par IP est posé sur la gateway. En production, **placer un TLS devant nginx** (Let's Encrypt, ou Azure App Gateway / Front Door).

## Sauvegardes

Le service `db-backup` fait un `pg_dump` quotidien gzip des **8 bases** (rétention 7 j) dans `./backups/`. Restauration : voir l'en-tête de `scripts/backup-databases.sh`. À la migration Azure : bascule vers les backups managés (Flexible Server + PITR).

## Limites de ressources

Les limites mémoire de `docker-compose.prod.yml` (768 Mo par service, 2 Go pour whatsapp/Chromium) sont des **points de départ** — à ajuster avec des métriques réelles.
