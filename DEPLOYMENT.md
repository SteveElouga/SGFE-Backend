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

> ⚠️ Le workflow visé est **`_publish-image.yml`**, pas `ci.yml`. Fulcio dérive
> l'identité du certificat de `job_workflow_ref` — le workflow qui exécute
> réellement le job de signature — et non du workflow appelant. Depuis que la
> publication est un workflow réutilisable, l'identité signée est
> `_publish-image.yml@…`.
>
> Les images sont signées avec `cosign sign --recursive` : la liste de manifestes
> **et** chaque variante par architecture. Sans ce drapeau, la vérification
> porterait sur un index et ne prouverait rien de l'image arm64 réellement
> exécutée sur la machine.

```sh
cosign verify \
  --certificate-identity-regexp "https://github.com/${GHCR_REPO}/.github/workflows/_publish-image.yml@.*" \
  --certificate-oidc-issuer https://token.actions.githubusercontent.com \
  "ghcr.io/${GHCR_REPO}/auth-service:${IMAGE_TAG}"
```

## Migrations

**En développement**, automatiques : chaque service Django lance `migrate` à son démarrage (`command:` dans `docker-compose.yml`).

**En production, non.** `docker-compose.prod.yml` remplace la commande par `python manage.py grpc_server` seul, et c'est `cd-prod.yml` qui joue les migrations en une étape distincte, avant le redémarrage. Huit services qui migrent en parallèle au démarrage, c'est huit verrous concurrents sur des schémas différents et aucun endroit où lire l'échec.

## Réseau & point d'entrée

### Côté backend

Seul **nginx** est publié (`:8080` → `gateway:8000`). Les ports internes (gRPC `50051-50058`, PostgreSQL `5432-5439`, Redis) **ne sont pas publiés**. Un rate limiting par IP est posé sur la gateway.

### La jointure avec le frontend

Le frontend est un **autre dépôt et un autre projet Compose** — donc, par défaut, un autre réseau, étanche. C'est le réseau **`sgfe-edge`** qui les relie :

```
navigateur ──► nginx du frontend  ──┐
               (sert le build       │  réseau sgfe-edge
                Angular, publié     └──► gateway:8000
                en :80/:443)

               nginx du backend ─────► gateway:8000   (arête API, :8080,
                                                       hors chemin navigateur)
```

- Ce dépôt **crée** `sgfe-edge` (bloc `networks` de `docker-compose.yml`) ; **seul** le service `gateway` y adhère.
- Le dépôt frontend le déclare `external: true` et y raccroche ses conteneurs.
- **Cette pile démarre donc en premier.** Sinon le `docker compose up` du frontend échoue en disant que le réseau n'existe pas — un échec lisible, préférable à un nginx qui démarre et sert des 502.

Le nginx **du frontend** est le point d'entrée du navigateur : c'est lui qui proxyfie `/graphql` (WebSocket compris), `/espace-abonne/`, les PDF de factures, les CSV de rapports et le bilan des impayés. Le nginx de ce dépôt ne sert pas le build Angular — il n'a ni `root` ni `try_files`, et son CSP `default-src 'none'` bloquerait tout script.

**Placer le TLS devant le nginx du frontend** (points 20-21 du registre), pas devant celui-ci.

### `FRONTEND_URL` — obligatoire en production

`auth-service` et `notification-service` construisent des liens envoyés à l'extérieur : activation de compte, réinitialisation de mot de passe, et surtout le lien de l'espace abonné poussé par WhatsApp (`{FRONTEND_URL}/espace/{token}`).

Le compose de développement pose `http://localhost:4200`. L'overlay de production rend la variable **obligatoire** (`${FRONTEND_URL:?…}`) : sans elle, `docker compose config` échoue en le disant, au lieu d'hériter en silence d'une valeur qui enverrait chaque abonné sur un lien inouvrable.

## Sauvegardes

Le service `db-backup` fait un `pg_dump` quotidien gzip des **8 bases** (rétention 7 j) dans `./backups/`. Restauration : voir l'en-tête de `scripts/backup-databases.sh`. À la migration Azure : bascule vers les backups managés (Flexible Server + PITR).

## Limites de ressources

Les limites mémoire de `docker-compose.prod.yml` (768 Mo par service, 2 Go pour whatsapp/Chromium) sont des **points de départ** — à ajuster avec des métriques réelles.
