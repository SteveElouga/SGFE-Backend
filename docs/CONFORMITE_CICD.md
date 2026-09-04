# Conformité CI aux standards internationaux — SGFE (backend + frontend)

> Périmètre : **CI uniquement** (pas le CD). Deux dépôts évalués : `SGFE-Backend`
> et `SGFE-Frontend`. Chaque verdict cite le fichier et la ligne exacts, ou la
> sortie brute d'une commande `gh api` exécutée le jour de la rédaction
> (4 septembre 2026). Aucune affirmation de conformité n'est faite sans preuve
> vue directement dans le code ou renvoyée par l'API GitHub — un point non
> vérifiable est marqué ❓, jamais deviné.
>
> Légende : ✅ Conforme · 🟡 Partiel · ❌ Non conforme · ➖ Non applicable · ❓ Non vérifié.

---

## 1. Résumé exécutif

Les deux CI sont **techniquement plus riches que la moyenne** d'un projet de
cette taille : tests par service avec couverture ≥ 80 %, `mypy --strict`,
`ruff`, `bandit`, `pip-audit` côté backend ; TypeScript en mode build réel,
tests unitaires, Playwright et scan Trivy côté frontend ; scan de secrets
(`gitleaks`) sur les deux ; SBOM + provenance + signature `cosign` keyless
(OIDC) sur les images backend. C'est un outillage solide.

Le problème n'est **pas l'outillage, c'est la gouvernance du flux** : ce que
ces outils trouvent ne bloque pas toujours ce qui est fusionné, publié ou
déployé. C'est exactement le type d'écart qu'un audit « impression générale »
manque, et que celui-ci a été chargé de vérifier ligne par ligne.

**Les 5 écarts les plus critiques, par ordre de gravité :**

1. **L'image scannée par Trivy n'est jamais celle qui est publiée ni déployée
   (backend ET frontend).** Côté backend, `publish-*` (qui pousse et signe
   l'image sur GHCR) ne dépend PAS de `docker-build-*` (qui la scanne) dans le
   graphe de jobs — les deux reconstruisent l'image séparément, avec des
   scopes de cache différents (`ci.yml:675` vs `ci.yml:567`). Une CVE
   CRITICAL/HIGH détectée ne bloque donc rien. Côté frontend, c'est pire :
   l'image réellement déployée (construite dans `cd-canary.yml`/`cd-staging.yml`)
   n'est **scannée nulle part**, le seul Trivy du dépôt tournant sur une image
   jetable non poussée (`ci.yml:100-145`). C'est une non-conformité directe à
   **OWASP CICD-SEC-9** (Improper Artifact Integrity Validation).
2. **Zéro revue humaine obligatoire pour fusionner, vérifié par API.**
   `required_approving_review_count: 0` sur `develop` (backend), et **aucune**
   protection de branche n'existe sur `main` du backend ni sur `develop`/`main`
   du frontend (`gh api .../branches/{main,develop}/protection` → `404 Branch
   not protected` pour les trois). C'est le risque n°1 de l'OWASP CI/CD Top 10
   (**CICD-SEC-1**) et l'échec du niveau **SLSA Source L4** (Two-Party Review).
3. **`release.yml` (backend) rompt la discipline d'épinglage du reste du
   dépôt** : `commitizen-tools/commitizen-action@master` — une référence de
   **branche mutable**, pas même un tag — tourne avec un `GITHUB_TOKEN` en
   `contents: write` (`release.yml:19-23`). Confirmé aggravant : le dépôt a
   `sha_pinning_required: false` (vérifié par `gh api .../actions/permissions`),
   donc rien n'empêche une régression similaire ailleurs.
4. **Dependabot security updates désactivé sur les deux dépôts**, vérifié via
   `security_and_analysis.dependabot_security_updates.status: "disabled"` —
   alors que les deux dépôts sont **publics** (`visibility: "PUBLIC"`,
   confirmé par API), ce qui aggrave l'exposition (CVE publiques, cibles
   identifiables). Aucun `CODEOWNERS` côté backend, aucun `SECURITY.md` sur
   aucun des deux dépôts pourtant publics (**CIS 1.2.1**).
5. **Le "gate" d'approbation de `cd-prod.yml` (backend) n'existe pas** :
   `environment: { name: production }` est déclaré (`cd-prod.yml:148-149`)
   mais `gh api repos/.../environments` renvoie **0 environnement configuré**
   — GitHub le créera à la volée, sans aucune règle de protection, à la
   première exécution. *(Point CD, hors périmètre de cet audit, mais assez
   frappant pour être noté ici en une ligne comme demandé.)*

**Niveau de maturité honnête :** CI **techniquement avancée sur le build**
(SLSA Build L2 atteignable côté backend pour les images signées), mais
**gouvernance de flux immature** (CICD-SEC-1 et CICD-SEC-9 en échec net) et
**hygiène de dépôt incomplète** (pas de CODEOWNERS ni SECURITY.md côté
backend, pas de Dependabot alerts actif nulle part, pas d'allowlist
d'actions tierces). Le frontend est en retard sur le backend sur presque
tous les axes sauf un — c'est le seul des deux à faire tourner de vrais
tests e2e Playwright en CI.

**Nuance nécessaire sur le point 2** : ce dépôt est un projet **mono-
développeur** (`gh api repos/.../collaborators` ne renvoie qu'un seul compte,
`SteveElouga` — pas d'organisation GitHub, MFA non vérifiable au niveau
plateforme). Exiger `required_approving_review_count: 1` sans un second
compte humain bloquerait purement et simplement toute fusion, l'auteur d'une
PR ne pouvant pas valider sa propre revue. Ce n'est donc pas une case à
cocher trivialement — voir la recommandation détaillée en §2.1 (CICD-SEC-1).

---

## 2. Grille détaillée par référentiel

### 2.1 OWASP Top 10 CI/CD Security Risks (CICD-SEC-1 à 10)

Source : [OWASP Top 10 CI/CD Security Risks](https://owasp.org/www-project-top-10-ci-cd-security-risks/)
(projet stable depuis octobre 2022, pas de date de mise à jour affichée sur
la page projet elle-même — vérifié le 4 septembre 2026 ; c'est la version en
vigueur, aucune v2 publiée à ce jour).

#### CICD-SEC-1 — Insufficient Flow Control Mechanisms

- **Backend** — ❌ **Non conforme.**
  Preuve : `gh api repos/SteveElouga/SGFE-Backend/branches/develop/protection`
  → `"required_pull_request_reviews":{"required_approving_review_count":0,
  "require_code_owner_reviews":false,"dismiss_stale_reviews":false}`. Sur
  `main`, la clé `required_status_checks` est **absente** de la réponse —
  aucun check n'est requis avant fusion sur la branche de production.
  `required_signatures.enabled:false` (pas de commit signé exigé) sur les
  deux branches.
  Manque : aucune revue humaine, aucune CI requise sur `main`.
  Recommandation : vu le contexte mono-développeur (voir résumé exécutif),
  `required_approving_review_count: 1` n'est pas actionnable seul. Deux
  options réalistes : (a) inviter un second compte (même un compte de revue
  automatisée avec droits limités) pour permettre une vraie exigence de
  revue ; (b) à défaut, documenter explicitement le risque accepté et
  compenser par `required_status_checks.contexts` couvrant **tous** les
  jobs qui comptent (voir CICD-SEC-9 ci-dessous) + `required_conversation_
  resolution: true` (actuellement `false`) + `required_signatures: true`
  (commits signés, traçabilité minimale en l'absence de revue tierce). Dans
  tous les cas, ajouter `required_status_checks` sur `main` — son absence
  totale n'a pas d'équivalent défendable, mono-développeur ou non.

- **Frontend** — ❌ **Non conforme.**
  Preuve : `gh api repos/SteveElouga/SGFE-Frontend/branches/develop/protection`
  et `.../main/protection` → `{"message":"Branch not protected","status":"404"}`
  pour les deux. Un CI complet existe (`ci.yml`, 4 jobs) mais **rien ne
  l'impose** : push direct sur `main`/`develop` possible, fusion sans CI
  verte possible.
  Recommandation : appliquer `scripts/setup-branch-protection.sh` (présent
  côté backend, absent côté frontend — à porter) en renseignant
  `required_status_checks.contexts` avec les 4 noms de jobs réels
  (`Typecheck & Build`, `Tests unitaires`, `Docker build`, `Playwright E2E`)
  — le script backend les laisse à `[]` par défaut (`setup-branch-
  protection.sh:34`), ce qui ne protégerait rien tant que ce n'est pas
  complété.

#### CICD-SEC-2 — Inadequate Identity and Access Management

- **Backend** — ✅ **Conforme** pour les identités de pipeline.
  Preuve : OIDC keyless partout où une identité cloud/signature est
  nécessaire — `cosign` sans clé stockée (`_publish-image.yml:36-41,79-91`,
  commentaire explicite « Signature cosign sans clé : le jeton OIDC du job
  EST l'identité signée »), AWS assumé via `aws-actions/configure-aws-
  credentials` + `role-to-assume` (`cd-prod.yml:158-161`, hors périmètre CD
  mais illustre la même discipline). Permissions scoping par job
  (`id-token: write` seulement où nécessaire, ex. `ci.yml:678-681`).
  🟡 Partiel sur l'IAM humaine : un seul compte a accès en écriture (voir
  résumé exécutif), donc pas de séparation des rôles humains vérifiable.

- **Frontend** — 🟡 **Partiel.**
  Le CI n'a besoin d'aucune identité cloud (pas de déploiement dans
  `ci.yml`). Mais les workflows de déploiement du même dépôt (`cd-canary.yml`,
  `cd-staging.yml`, `rollback.yml`) utilisent un **secret SSH statique**
  (`secrets.SSH_PRIVATE_KEY`, ex. `cd-canary.yml:75`) plutôt qu'OIDC — point
  CD, noté ici en une ligne seulement car hors périmètre strict de cette
  tâche, mais c'est l'exact opposé du choix du backend documenté dans
  `cd-prod.yml:11-15`.

#### CICD-SEC-3 — Dependency Chain Abuse

- **Backend** — 🟡 **Partiel.**
  Preuve de conformité : `ci.yml`, `_build-scan.yml`, `_publish-image.yml`
  épinglent **systématiquement** chaque action par SHA complet avec
  commentaire de version (ex. `ci.yml:58` `actions/checkout@34e114876b...
  # v4`). Dependabot est configuré pour faire avancer ces SHA
  automatiquement (`dependabot.yml:165-171`, écosystème `github-actions`).
  Preuve de non-conformité, dans le **même dépôt** : `release.yml` ne suit
  pas cette discipline — `actions/checkout@v4` (`release.yml:16`, tag
  mutable), `commitizen-tools/commitizen-action@master` (`release.yml:21`,
  **référence de branche**, la forme la plus dangereuse : n'importe quel
  push futur sur `master` de ce dépôt tiers s'exécute directement avec
  `contents: write`), `softprops/action-gh-release@v3` (`release.yml:26`,
  tag mutable). Aggravant : `gh api repos/SteveElouga/SGFE-Backend/actions/
  permissions` → `"sha_pinning_required":false` — le dépôt ne peut pas
  s'auto-protéger contre une régression de ce type.
  Recommandation : épingler les 3 actions de `release.yml` par SHA (comme
  le reste du dépôt), remplacer `@master` par un tag de release
  `commitizen-tools/commitizen-action`, puis activer `sha_pinning_required:
  true` au niveau du dépôt (fonctionnalité GitHub disponible depuis août
  2025, cf. §2.5) pour que toute régression future échoue au lieu de passer.

- **Frontend** — ❌ **Non conforme.**
  Preuve : sur les 4 workflows, **seul** `aquasecurity/trivy-action` est
  épinglé par SHA (`ci.yml:139`, identique au backend) ; toutes les autres
  actions utilisent un tag mutable : `actions/checkout@v4` (`ci.yml:20`),
  `actions/setup-node@v4` (`ci.yml:22`), `docker/build-push-action@v6`
  (`ci.yml:116`), `docker/setup-buildx-action@v3`, `appleboy/ssh-action@v1`
  (CD, `cd-canary.yml:71` etc.). Même `gh api .../actions/permissions` :
  `"sha_pinning_required":false`.
  Recommandation : reprendre le patron du backend (SHA + commentaire de
  version) sur les 4 fichiers, en s'appuyant sur `dependabot.yml`
  écosystème `github-actions` — actuellement **absent du dépôt frontend**
  (aucun fichier trouvé, aucune mise à jour automatique des dépendances
  npm/actions non plus).

#### CICD-SEC-4 — Poisoned Pipeline Execution (PPE)

- **Backend** — ✅ **Conforme.**
  Preuve : déclencheurs `push`/`pull_request` uniquement (`ci.yml:3-7`),
  **aucune occurrence** de `pull_request_target` dans les 5 workflows du
  dépôt (vérifié par lecture complète). Un fork de ce dépôt public ne peut
  donc pas exécuter de code avec les secrets du dépôt cible.
- **Frontend** — ✅ **Conforme**, même constat (`ci.yml:4-7`), aucun
  `pull_request_target` dans les 4 workflows.

#### CICD-SEC-5 — Insufficient PBAC (Pipeline-Based Access Controls)

- **Backend** — 🟡 **Partiel.** `cd-prod.yml` expose un `workflow_dispatch`
  avec un input `image_tag` libre (`cd-prod.yml:44-49`) — n'importe quel
  collaborateur avec accès en écriture peut redéployer n'importe quel tag
  d'image déjà publié. Le seul garde-fou est la vérification de signature en
  aval (`cd-prod.yml:95-138`), pas un contrôle d'accès sur le déclenchement
  lui-même. Point CD, mentionné pour mémoire.
  Côté CI stricte : pas de `workflow_dispatch` sur `ci.yml`, rien à redouter
  côté déclenchement manuel.
- **Frontend** — ❓ **Non vérifié en profondeur** (hors périmètre CD) —
  `rollback.yml` a un `workflow_dispatch` sans validation de format sur
  `image-tag` (`rollback.yml:5-9`), même famille de remarque que ci-dessus.

#### CICD-SEC-6 — Insufficient Credential Hygiene

- **Backend** — ✅ **Conforme** sur la CI elle-même.
  Preuve : `ci.yml:16-39` documente explicitement pourquoi chaque secret
  factice utilisé en test n'est PAS un vrai secret (format, portée,
  justification ligne par ligne) — un exemple rare de discipline de
  documentation sur ce point précis. `PII_ENCRYPTION_KEY` n'est même pas mis
  en variable d'environnement statique mais généré à la volée
  (`ci.yml:258-259`, `openssl rand -base64 32`) précisément pour ne jamais
  matcher un pattern de détection de secret figé. `gitleaks` tourne sur
  l'historique complet (`fetch-depth: 0`, `ci.yml:112`).
- **Frontend** — ➖ **Non applicable côté CI** : `ci.yml` n'utilise aucun
  secret. (Le secret SSH long-lived des workflows de déploiement du même
  dépôt est un point CD, déjà noté en CICD-SEC-2.)

#### CICD-SEC-7 — Insecure System Configuration

- **Backend** — 🟡 **Partiel.**
  Points conformes : `permissions: contents: read` au niveau workflow
  (`ci.yml:13-14`), élevé seulement job par job où nécessaire (ex.
  `ci.yml:678-681`) ; `gh api .../actions/permissions/workflow` confirme
  `"default_workflow_permissions":"read"` au niveau du dépôt (défense en
  profondeur). Concurrency correctement configurée (`ci.yml:9-11`, annule
  les runs obsolètes ; `cd-prod.yml:51-55`, **refuse** d'annuler un
  déploiement en cours — bon réflexe, deux comportements opposés choisis à
  bon escient).
  Point manquant : **aucun `timeout-minutes`** sur les 13 jobs directs de
  `ci.yml` (`changes`, `secrets-scan`, `check-grpc-lib-drift`, les 10 jobs
  `test-*`) — seuls les workflows réutilisables `_build-scan.yml:39` (30 min)
  et `_publish-image.yml:35` (45 min) en déclarent un. Sans limite, un job
  qui pend consomme jusqu'à 6h (valeur par défaut GitHub) avant d'échouer.
  Recommandation : ajouter `timeout-minutes: 15` (ou une valeur mesurée) sur
  les 13 jobs directs de `ci.yml`.
- **Frontend** — 🟡 **Partiel.**
  `timeout-minutes` **est** posé sur les 4 jobs (`ci.yml:18,84,103,152`,
  20/15/20/20 min) — meilleure hygiène que le backend sur ce point précis.
  Mais **aucun bloc `permissions:`** n'est déclaré dans `ci.yml` (vérifié :
  absent des 185 lignes du fichier) — contrairement au backend qui le fait
  explicitement. Le token effectif reste en lecture seule uniquement parce
  que le réglage du dépôt (`default_workflow_permissions:"read"`) le
  impose — un réglage externe au fichier, pas une garantie du fichier
  lui-même. Recommandation : ajouter explicitement `permissions: contents:
  read` en tête de `ci.yml`, pour que la sécurité ne dépende pas d'un
  paramètre de dépôt qui pourrait changer sans que le code du workflow ne
  bouge.

#### CICD-SEC-8 — Ungoverned Usage of 3rd Party Services

- **Backend et Frontend** — ❌ **Non conforme** (identique sur les deux).
  Preuve : `gh api repos/SteveElouga/SGFE-{Backend,Frontend}/actions/
  permissions` → `"allowed_actions":"all"` sur les deux dépôts. N'importe
  quelle action de n'importe quel dépôt public GitHub peut être ajoutée à un
  workflow sans validation — aucune allowlist (`selected` +
  `allowed_actions` restreinte à une liste de créateurs vérifiés ou de
  dépôts précis).
  Recommandation : passer `allowed_actions` à `selected` et lister
  explicitement les actions déjà utilisées (elles sont peu nombreuses et
  identifiées ci-dessus), ce qui aurait d'ailleurs empêché mécaniquement la
  régression `@master` de CICD-SEC-3.

#### CICD-SEC-9 — Improper Artifact Integrity Validation

- **Backend** — ❌ **Non conforme.** C'est l'écart n°1 du résumé exécutif.
  Preuve précise : `publish-auth` déclare `needs: [changes, test-auth,
  secrets-scan]` (`ci.yml:676`) — **sans** `docker-build-auth` dans la
  liste. Ce patron est identique pour les 9 autres services (`publish-config`
  `ci.yml:688`, `publish-reporting` `ci.yml:700`, `publish-gateway`
  `ci.yml:712`, `publish-abonne` `ci.yml:724`, `publish-facturation`
  `ci.yml:736`, `publish-campagne` `ci.yml:748`, `publish-paiement`
  `ci.yml:760`, `publish-notification` `ci.yml:772`, `publish-nginx`
  `ci.yml:784`, `publish-whatsapp` `ci.yml:796` — aucun ne dépend de son
  `docker-build-*` correspondant). De plus, ce sont deux **builds
  distincts** de la même source : `_build-scan.yml:65-76` construit une
  image mono-architecture, `load: true`, jamais poussée, avec un scope de
  cache `${{ inputs.service }}-${{ matrix.suffixe }}` ; `_publish-image.yml:
  58-77` reconstruit **séparément**, multi-architecture en un seul appel
  buildx, scope de cache `${{ inputs.service }}-multi`. Ce sont deux
  ensembles de couches d'image potentiellement différents (Docker ne
  garantit pas la reproductibilité bit à bit par défaut). Conséquence
  directe : une CVE CRITICAL/HIGH détectée par Trivy sur l'image de test
  n'empêche ni la construction, ni la signature `cosign`, ni la publication
  sur GHCR de l'image réellement utilisée par `cd-prod.yml`. Le job requis
  `ci-status` (`ci.yml:658-668`) ne référence d'ailleurs **aucun**
  `docker-build-*` dans sa liste `needs` — même en spéculant que quelqu'un
  ajoute cette dépendance à `publish-*`, le scan resterait de toute façon
  hors du chemin qui bloque la fusion de PR.
  Recommandation (actionnable) : construire l'image **une seule fois**
  (multi-arch, dans un job unique appelé depuis `ci.yml`), scanner **cette**
  image poussée vers un registre (ou conservée en artefact le temps du
  scan), puis ne signer/republier que si le scan passe — ou, a minima, faire
  dépendre chaque `publish-*` de son `docker-build-*` (`needs: [changes,
  test-X, secrets-scan, docker-build-X]`) pour au moins bloquer la
  publication sur un échec du scan, même si les deux builds restent
  distincts.

- **Frontend** — ❌ **Non conforme, plus sévère que le backend.**
  Preuve : le seul job Trivy du dépôt (`ci.yml:100-145`, `docker-build`)
  scanne une image construite avec `push: false` (`ci.yml:119`) — jamais
  poussée nulle part. Les workflows qui construisent réellement l'image
  déployée, `cd-canary.yml:50-58` et `cd-staging.yml:48-56`, appellent
  `docker/build-push-action` avec `push: true` **sans aucune étape Trivy**
  ni avant ni après (confirmé par lecture complète des 4 fichiers `.github/
  workflows/*.yml` du frontend — `trivy` n'apparaît que dans `ci.yml`).
  L'image réellement servie aux utilisateurs n'est donc **jamais scannée**,
  à aucun stade.
  Recommandation : ajouter l'étape Trivy (même bloc que `ci.yml:138-145`)
  directement dans `cd-canary.yml`/`cd-staging.yml` juste après le build, en
  faisant échouer le job sur CRITICAL/HIGH avant le `push`, ou refactorer
  pour un build unique partagé entre CI et CD (comme recommandé côté
  backend).

#### CICD-SEC-10 — Insufficient Logging and Visibility

- **Backend** — 🟡 **Partiel.** Les runs GitHub Actions sont journalisés
  nativement (rétention par défaut GitHub, non configurée explicitement ici
  — aucun réglage de rétention de logs de run trouvé). Le job `ci-status`
  produit un résumé explicite en cas d'échec (`ci.yml:663-668`). Pas de
  centralisation vers un SIEM/observabilité externe — cohérent avec le
  constat plus large déjà fait dans `CLAUDE.md` (§ Observabilité : « AUCUNE
  aujourd'hui »), donc pas un écart spécifique à la CI mais un symptôme du
  même manque.
- **Frontend** — 🟡 **Partiel**, même constat : logs natifs GitHub Actions
  uniquement, rapport Playwright conservé 14 jours (`ci.yml:178-184`,
  `retention-days: 14`) — bonne pratique pour le post-mortem d'un test e2e
  qui échoue, mais toujours pas de visibilité centralisée sur les
  exécutions de pipeline elles-mêmes.

---

### 2.2 SLSA (Supply-chain Levels for Software Artifacts)

Source : [slsa.dev](https://slsa.dev/) — version en vigueur **v1.2**,
publiée le **24 novembre 2025** ([annonce officielle](https://slsa.dev/blog/2025/11/announce-slsa-v1.2)),
qui ajoute un **Source Track** au **Build Track** existant depuis v1.0.
Build Track : [slsa.dev/spec/v1.0/levels](https://slsa.dev/spec/v1.0/levels)
(inchangé en substance en v1.2). Source Track :
[slsa.dev/spec/v1.2/source-requirements](https://slsa.dev/spec/v1.2/source-requirements).

#### Build Track (artefacts — images Docker)

- **Build L1 — Provenance Exists.**
  - **Backend** — ✅ **Conforme.** `_publish-image.yml:76-77` déclare
    `sbom: true` et `provenance: true` sur `docker/build-push-action`, qui
    génère une attestation de provenance décrivant la plateforme et le
    processus de build. Distribuée avec l'image (attestation OCI attachée
    au manifeste).
  - **Frontend** — ❓ **Non vérifié.** Ni `cd-canary.yml:50-58` ni
    `cd-staging.yml:48-56` ne déclarent `provenance:`/`sbom:` explicitement.
    Buildx peut attacher une provenance minimale par défaut selon la
    version du driver, mais je n'ai pas accès au registre pour inspecter le
    manifeste réellement publié — je ne peux donc ni confirmer ni infirmer
    qu'une provenance existe en pratique, et rien dans le code ne la
    demande explicitement. Recommandation : ajouter `sbom: true` et
    `provenance: true` explicitement, ne pas compter sur un défaut implicite
    non vérifié.

- **Build L2 — Hosted Build Platform (provenance signée, liée à la
  plateforme).**
  - **Backend** — ✅ **Conforme.** Build sur runner hébergé GitHub
    (`_publish-image.yml:32`, `runs-on: ubuntu-latest`), provenance liée à
    cette identité par signature `cosign` keyless (`_publish-image.yml:
    79-91`) dont l'identité OIDC (émise par `token.actions.githubusercontent.com`)
    est ensuite **vérifiée et ancrée** sur le workflow exact
    (`cd-prod.yml:133-135`, `--certificate-identity-regexp` pointant sur
    `_publish-image.yml@` — le commentaire `cd-prod.yml:119-132` explique
    d'ailleurs très précisément pourquoi c'est `_publish-image.yml` et pas
    `ci.yml` qui doit apparaître dans le motif, preuve d'une vraie
    compréhension du mécanisme Fulcio/`job_workflow_ref`). C'est une mise en
    œuvre correcte et bien comprise du critère L2.
  - **Frontend** — ❌ **Non conforme.** Aucune signature `cosign` ni
    équivalent dans les 4 workflows du dépôt (confirmé par lecture
    complète — `cosign` n'apparaît nulle part).

- **Build L3 — Hardened Builds (isolation empêchant un run d'en influencer
  un autre, secret de signature inaccessible aux étapes de build définies
  par l'utilisateur).**
  - **Backend** — ❌ **Non conforme**, et c'est un vrai écart (pas
    seulement l'absence d'un label). Le build (`docker/build-push-action`,
    `_publish-image.yml:58-77`) et la signature (`cosign`,
    `_publish-image.yml:79-91`) tournent dans le **même job**, sur le
    **même runner**, avec le même jeton OIDC (`id-token: write`,
    `_publish-image.yml:40`) accessible pendant toute la durée du job — y
    compris pendant les étapes de build elles-mêmes. Une étape de build
    compromise (image de base piégée, action tierce compromise plus haut
    dans le même job) pourrait en théorie interagir avec ce jeton avant que
    `cosign` ne l'utilise. C'est précisément le problème que L3 vise à
    éliminer par une isolation stricte entre build et signature.
    Recommandation : adopter `slsa-framework/slsa-github-generator` (le
    générateur officiel, qui isole la génération de provenance dans un
    workflow réutilisable séparé et dédié, hors de portée des étapes de
    build définies par le projet) plutôt qu'un `cosign sign` exécuté dans le
    même job que le build.
  - **Frontend** — ➖ **Non applicable** (aucune provenance/signature à
    durcir, cf. L1/L2 ci-dessus).

#### Source Track (nouveau en v1.2, novembre 2025 — référentiel très récent)

Le « SCS » (Source Control System) ici est GitHub. Évalué globalement pour
les deux dépôts, avec les différences notées.

- **Source L1 — Version Controlled.** ✅ **Conforme** pour les deux dépôts :
  Git sur GitHub, révisions immuables et identifiables par hash, diffs
  lisibles, gestion d'identité par compte GitHub. Aucune preuve contraire.
- **Source L2 — History & Provenance** (historique de branche continu et
  immuable + attestations de provenance de source émises par le SCS
  à chaque mise à jour). — ❓ **Non vérifiable en l'état.** GitHub, à ma
  connaissance et à la date de cet audit, n'émet pas nativement
  d'attestation de provenance de source au sens strict défini par SLSA v1.2
  (fonctionnalité trop récente — spec publiée il y a moins d'un an). Ce
  n'est pas imputable au projet : c'est l'écosystème qui n'a pas encore de
  support natif généralisé. Sur la partie vérifiable : `allow_force_pushes:
  false` et `allow_deletions: false` tiennent sur `develop` et `main`
  (backend, vérifié par API) — cohérent avec l'exigence d'immuabilité de
  l'historique — mais **aucune protection n'existe sur le frontend**
  (voir CICD-SEC-1), donc rien n'empêche un force-push ou une suppression de
  branche côté frontend : ❌ **Non conforme** pour le frontend sur ce point
  précis, indépendamment de la question des attestations.
- **Source L3 — Continuous Technical Controls** et **Source L4 — Two-Party
  Review** — ❌ **Non conforme** pour les deux dépôts. L4 exige
  explicitement que deux personnes de confiance valident tout changement
  sur les branches protégées — voir CICD-SEC-1 ci-dessus
  (`required_approving_review_count: 0` backend, aucune protection
  frontend). Même nuance mono-développeur qu'en §2.1.

---

### 2.3 NIST SP 800-218 (Secure Software Development Framework v1.1)

Source : [NIST SP 800-218 (PDF officiel)](https://nvlpubs.nist.gov/nistpubs/SpecialPublications/NIST.SP.800-218.pdf),
version 1.1, complétée par [CSRC — projet SSDF](https://csrc.nist.gov/projects/ssdf).
Pratiques extraites directement du texte du document (recherche de motif sur
le PDF, pas de mémoire). Sélection des pratiques applicables à une CI (PS,
PW, RV) ; PO (Prepare the Organization) est hors périmètre CI par nature.

- **PS.1 — Protect All Forms of Code from Unauthorized Access and
  Tampering** (`PS.1.1` : stocker tout code — source, exécutable,
  configuration-as-code — dans un dépôt et restreindre l'accès).
  - **Backend** — 🟡 **Partiel.** Le code est dans Git avec accès
    restreint (dépôt public en lecture, écriture restreinte au propriétaire
    — mais restreinte par défaut GitHub, pas par une politique explicite,
    et sans revue obligatoire, voir CICD-SEC-1). Les secrets réels sont
    hors dépôt par conception documentée (`ci.yml:16-39`).
  - **Frontend** — 🟡 **Partiel**, même constat, aggravé par l'absence
    totale de protection de branche.

- **PS.2 — Provide a Mechanism for Verifying Software Release Integrity**
  (rendre disponible aux consommateurs un moyen de vérifier l'intégrité).
  - **Backend** — ✅ **Conforme.** Signature `cosign` vérifiable
    publiquement via Rekor (transparence Sigstore), plus SBOM attaché
    (`_publish-image.yml:76-77`). `cd-prod.yml:108-138` **consomme
    effectivement** cette vérification avant déploiement — donc le
    mécanisme n'est pas seulement produit, il est utilisé.
  - **Frontend** — ❌ **Non conforme.** Aucun mécanisme de vérification
    d'intégrité des images publiées (pas de signature, provenance non
    confirmée — voir SLSA L1/L2 ci-dessus).

- **PS.3 — Archive and Protect Each Software Release** (y compris
  `PS.3.2` : collecter et partager les données de provenance).
  - **Backend** — 🟡 **Partiel.** Les images sont conservées sur GHCR,
    taguées par SHA de commit ET par nom de branche
    (`_publish-image.yml:67-69`), ce qui permet de retrouver n'importe
    quelle release passée. Pas de politique de rétention/expiration des
    tags documentée dans le workflow lui-même.
  - **Frontend** — 🟡 **Partiel**, tags `sha-<sha>` et `staging-<sha>`
    similaires (`cd-canary.yml:46-48`, `cd-staging.yml:44-46`), même
    remarque.

- **PW.4 — Reuse Existing, Well-Secured Software** (`PW.4.4` : vérifier que
  les composants tiers acquis n'ont pas de vulnérabilités connues).
  - **Backend** — ✅ **Conforme.** `pip-audit` sur chaque service
    (ex. `ci.yml:178-179`), Dependabot version-updates hebdomadaire sur pip/
    docker/npm/github-actions (`dependabot.yml`, 19 entrées). 🟡 nuance :
    `dependabot_security_updates` (les alertes de vulnérabilité natives
    GitHub, distinctes des mises à jour programmées) est **désactivé**
    (`security_and_analysis.dependabot_security_updates.status:"disabled"`,
    vérifié par API) — donc la détection dépend uniquement de `pip-audit`
    exécuté à chaque run de CI, pas d'une alerte continue entre deux runs.
  - **Frontend** — ❌ **Non conforme.** Aucun fichier Dependabot/Renovate
    trouvé dans le dépôt (recherche exhaustive), et la même désactivation
    des security updates natives s'applique. Le `npm audit` n'apparaît nulle
    part dans `ci.yml` (vérifié : ni `npm audit` ni équivalent).
    Recommandation : ajouter `npm audit --audit-level=high` (ou `audit-ci`)
    comme étape de `ci.yml`, et créer un `dependabot.yml` frontend
    (écosystèmes `npm` et `github-actions` a minima).

- **PW.6 — Configure the Compilation, Interpreter, and Build Toolchains
  Securely** (utiliser des versions à jour des outils de build, activer les
  options qui produisent des avertissements de sécurité).
  - **Backend** — ✅ **Conforme.** Versions d'outils explicitement épinglées
    (`ruff==0.15.20`, Python 3.12, `ci.yml:171`), `mypy --strict` activé
    partout (ex. `ci.yml:176-177`) — un mode strict est exactement le type
    de configuration que PW.6.2 vise.
  - **Frontend** — 🟡 **Partiel.** `tsc -b` (mode build réel, pas
    `--noEmit` seul — bug historique documenté et corrigé, voir `CLAUDE.md`
    frontend § Budgets/commandes) constitue un vrai contrôle de type strict.
    Mais aucune option de compilateur « stricte » n'est vérifiable
    directement depuis les workflows (dépend de `tsconfig.json`, hors
    périmètre de cette lecture des seuls workflows CI).

- **PW.7 — Review and/or Analyze Human-Readable Code to Identify
  Vulnerabilities** (analyse statique et/ou revue humaine).
  - **Backend** — 🟡 **Partiel.** `bandit` tourne sur chaque service
    (ex. `ci.yml:180-181`, exclusions ciblées sur `proto/`/`migrations`) —
    une analyse statique de sécurité réelle. Mais **pas de revue humaine
    obligatoire** (voir CICD-SEC-1) et pas de SAST plus profond type CodeQL/
    Semgrep — `bandit` est un bon point de départ pour Python mais a une
    couverture de règles plus limitée.
  - **Frontend** — ❌ **Non conforme.** Aucun outil d'analyse statique de
    sécurité dans `ci.yml` (ni ESLint avec règles de sécurité, ni Semgrep,
    ni CodeQL) — seul `tsc` (vérification de types, pas de sécurité) et le
    build de production sont exécutés côté qualité.
    Recommandation : ajouter au minimum `eslint-plugin-security` ou une
    analyse Semgrep/CodeQL au pipeline `quality`.

- **PW.8 — Test Executable Code to Identify Vulnerabilities** (tests
  fonctionnels ET tests axés sécurité).
  - **Backend** — 🟡 **Partiel.** Tests unitaires avec seuil de couverture
    strict (`coverage report -m --fail-under=80`, répété sur les 8 services
    testés) — bonne robustesse fonctionnelle. Mais aucun test dynamique de
    sécurité (DAST), et deux composants (`nginx`, `whatsapp-service`) n'ont
    **aucun** job de test dans `ci.yml` (seulement `docker-build-nginx`/
    `docker-build-whatsapp`, pas de `test-nginx`/`test-whatsapp` — vérifié
    par absence dans le fichier complet).
  - **Frontend** — ✅ **Conforme**, notablement meilleur que le backend sur
    ce point précis : tests unitaires (`ci.yml:81-97`) ET tests e2e
    Playwright réels (`ci.yml:147-184`), avec garde contre le faux-positif
    historique documenté en commentaire (`ci.yml:170-172`, suppression de
    `--pass-with-no-tests` qui rendait le job vert sur un dossier de tests
    vide).

- **RV.1 — Identify and Confirm Vulnerabilities on an Ongoing Basis**
  (surveiller en continu, pas seulement au moment du build).
  - **Backend et Frontend** — ❌ **Non conforme** sur la surveillance
    *continue* (entre deux runs de CI) : `dependabot_security_updates`
    désactivé sur les deux dépôts (API, déjà cité). La détection de
    vulnérabilité n'existe qu'au moment où quelqu'un déclenche la CI
    (`pip-audit`/Trivy backend, Trivy frontend) — une CVE publiée sur une
    dépendance déjà en production n'est signalée par rien tant qu'aucun push
    n'a lieu.
    Recommandation : activer Dependabot security updates au niveau du
    dépôt (réglage indépendant du fichier `dependabot.yml`, qui ne gère que
    les mises à jour programmées).

---

### 2.4 CIS Software Supply Chain Security Guide (v1.0, juin 2022)

Source : [CIS Software Supply Chain Security Guide](https://www.cisecurity.org/insights/white-papers/cis-software-supply-chain-security-guide)
(texte complet récupéré et lu, page de garde datée « v1.0 — June 2022 » —
c'est la version stable actuelle, aucune v2 publiée). Sélection des
contrôles applicables à la CI dans les sections **1 Source Code**,
**2 Build Pipelines** et **3 Dependencies** ; **4 Artifacts** et
**5 Deployment** touchent surtout le registre et le déploiement — exclus
sauf mention explicite au §1. Les contrôles d'organisation nécessitant une
org GitHub (MFA imposée, SSO, nombre minimal d'administrateurs, équipes) —
**1.3.2 à 1.3.12** — sont marqués **➖ Non applicable en bloc** : ces
dépôts appartiennent à un compte personnel (`gh api .../collaborators` ne
renvoie qu'un seul compte, `SteveElouga`), pas à une organisation GitHub, et
ces contrôles n'existent tout simplement pas à ce niveau de compte.

#### 1. Source Code

| Contrôle | Backend | Frontend |
|---|---|---|
| **1.1.3** Approbation de 2 utilisateurs authentifiés avant fusion | ❌ 0 revue requise (API) | ❌ pas de protection |
| **1.1.9** Tous les checks doivent passer avant fusion | 🟡 `ci-status` requis sur `develop` **mais absent sur `main`**, et n'inclut pas `docker-build-*` (§2.1 CICD-SEC-9) | ❌ aucun check requis |
| **1.1.11** Tous les commentaires résolus avant fusion | ❌ `required_conversation_resolution:false` (API) | ❌ pas de protection |
| **1.1.14** Protection de branche appliquée aux administrateurs | ✅ `enforce_admins:true` (API, les deux branches) | ❌ pas de protection |
| **1.1.16/17** Force-push et suppression de branche interdits | ✅ `allow_force_pushes:false`, `allow_deletions:false` (API) | ❌ pas de protection, donc rien n'interdit ces actions |
| **1.1.18** Tout code fusionné est scanné automatiquement | 🟡 scanné oui (voir CI), mais pas **bloquant** pour la fusion (`ci-status` requis n'inclut ni bandit/pip-audit en tant que checks séparés obligatoires — ils sont noyés dans `test-*`, qui LUI est requis, donc en réalité conforme pour bandit/pip-audit ; seul le scan Trivy des images reste hors gate, cf. CICD-SEC-9) | ❌ rien n'est requis |
| **1.2.1** `SECURITY.md` sur tout dépôt public | ❌ absent (dépôt confirmé `PUBLIC` par API) | ❌ absent (dépôt confirmé `PUBLIC` par API) |
| **1.3.4/1.3.5** MFA imposée aux contributeurs | ➖ N/A (compte personnel, pas d'org) | ➖ N/A (compte personnel, pas d'org) |
| **1.5.1** Scanner de secrets en pipeline | ✅ `gitleaks-action` (`ci.yml:107-115`) | ❓ non vérifié dans `ci.yml` (aucune trace de `gitleaks`/scanner de secrets trouvée par lecture complète) → **❌ Non conforme** |
| **1.5.2** Scanner les instructions de pipeline CI elles-mêmes (misconfig) | ❌ aucun outil type `actionlint`/`zizmor` trouvé | ❌ idem |
| **1.5.4** Scanner de vulnérabilités de code | 🟡 `bandit` (statique, limité) | ❌ aucun |
| **1.5.5** Scanner de vulnérabilités open-source (dépendances) | ✅ `pip-audit` par service | ❌ aucun (`npm audit` absent) |

#### 2. Build Pipelines

| Contrôle | Backend | Frontend |
|---|---|---|
| **2.1.1** Une responsabilité par pipeline | ✅ jobs séparés par service, `_build-scan.yml`/`_publish-image.yml` en workflows réutilisables dédiés | 🟡 4 jobs distincts, granularité correcte |
| **2.1.2** Infrastructure de pipeline immuable | ✅ runners GitHub-hébergés éphémères (`ubuntu-latest`, recréés à chaque run) | ✅ idem |
| **2.1.5/2.1.6** Accès restreint et authentifié à l'environnement de build | ✅ hérité de GitHub Actions (pas de configuration spécifique nécessaire ni trouvée en écart) | ✅ idem |
| **2.3.4** Changements aux fichiers de pipeline tracés et revus | ❌ pas de revue obligatoire (mêmes fichiers `.github/workflows/*` que le reste du code, soumis au même `required_approving_review_count:0`) | ❌ idem, aggravé par l'absence totale de protection |
| **2.3.6** Pipelines scannés pour mauvaise configuration | ❌ aucun outil trouvé (`actionlint`, `zizmor`, etc.) | ❌ idem |
| **2.3.7** Pipelines scannés pour vulnérabilités | 🟡 Trivy sur les images produites, pas sur le YAML lui-même | 🟡 idem |
| **2.4.1** Tous les artefacts de release signés | ✅ `cosign sign --recursive` (`_publish-image.yml:91`) — mais voir CICD-SEC-9 : signé ≠ scanné avant publication | ❌ aucune signature |
| **2.4.2** Dépendances externes du build verrouillées | ✅ `requirements.txt` versionnés, images de base épinglées par digest (ex. `gateway/Dockerfile:2`, `FROM python:3.12-slim@sha256:...`) | ❓ non vérifié (`package-lock.json` présumé présent mais non lu dans le cadre de cette tâche, qui porte sur les workflows) |
| **2.4.4** Le pipeline produit des artefacts reproductibles | ❌ non garanti, et la preuve directe est l'écart CICD-SEC-9 : deux builds distincts de la même source, sans vérification qu'ils produisent le même résultat | ❌ idem (build CI jetable vs build CD réel, jamais comparés) |
| **2.4.5** Le pipeline produit un SBOM | ✅ `sbom: true` (`_publish-image.yml:76`) | ❌ absent |
| **2.4.6** Le SBOM produit est signé | 🟡 signé indirectement si `cosign --recursive` couvre le manifeste d'attestation SBOM (non confirmé avec certitude depuis le YAML seul — voir la même réserve qu'en SLSA Build L3) | ➖ N/A (pas de SBOM) |

#### 3. Dependencies

| Contrôle | Backend | Frontend |
|---|---|---|
| **3.1.7** Dépendances épinglées à une version précise et vérifiée | ✅ `requirements.txt` avec versions figées (constat déjà documenté dans `AUDIT_SGFE.md`, revérifié ici sur `ci.yml:171` qui installe depuis ces fichiers) | ❓ non vérifié (`package.json`/lock non lu) |
| **3.2.2** Scan automatique des vulnérabilités connues des paquets | ✅ `pip-audit` | ❌ absent (`npm audit` absent de `ci.yml`) |
| **3.2.3** Scan automatique des implications de licence | ❌ aucun outil trouvé (ni `pip-licenses`, ni équivalent npm) | ❌ idem |

---

### 2.5 Guide de durcissement GitHub Actions (GitHub, documentation officielle)

Source : [Security hardening for GitHub Actions](https://docs.github.com/en/actions/security-for-github-actions/security-guides/security-hardening-for-github-actions)
(documentation vivante, pas de date de version affichée — consultée le
4 septembre 2026) ; [changelog SHA pinning enforcement, 15 août 2025](https://github.blog/changelog/2025-08-15-github-actions-policy-now-supports-blocking-and-sha-pinning-actions/).

| Recommandation GitHub | Backend | Frontend |
|---|---|---|
| `GITHUB_TOKEN` en lecture seule par défaut, élevé job par job | ✅ `ci.yml:13-14` + réglage dépôt `default_workflow_permissions:"read"` (API) | 🟡 pas déclaré dans le fichier (`ci.yml`), mais protégé par le même réglage de dépôt — voir CICD-SEC-7 |
| Épinglage des actions par SHA complet | 🟡 quasi systématique, sauf `release.yml` (voir CICD-SEC-3) | ❌ quasi jamais (seul Trivy épinglé) |
| Politique dépôt forçant l'épinglage (`sha_pinning_required`) | ❌ `false` (API) | ❌ `false` (API) |
| Éviter `pull_request_target` sur du code non fiable | ✅ jamais utilisé | ✅ jamais utilisé |
| OIDC plutôt que secrets long-lived pour le cloud | ✅ AWS via `role-to-assume` (`cd-prod.yml:158-161`, CD mais confirme la discipline) ; cosign keyless partout | ❌ SSH statique en CD (`secrets.SSH_PRIVATE_KEY`) — point CD, une ligne |
| Environnements protégés avec reviewers pour les secrets sensibles | ❌ 0 environnement configuré (API) malgré `cd-prod.yml:148-149` qui en présuppose un | 🟡 1 environnement (`staging`) existe mais `protection_rules:[]` — aucune règle réelle (API) |
| Allowlist des actions tierces autorisées | ❌ `allowed_actions:"all"` (API) | ❌ idem |

---

### 2.6 DORA / Accelerate — angle de maturité d'ingénierie

Source : [2025 DORA Report: State of AI-assisted Software Development](https://cloud.google.com/blog/products/ai-machine-learning/announcing-the-2025-dora-report)
et [Four Keys — Google Cloud](https://cloud.google.com/blog/products/devops-sre/using-the-four-keys-to-measure-your-devops-performance).
Les 4 métriques DORA (fréquence de déploiement, lead time, taux d'échec des
changements, MTTR) se mesurent sur des **données réelles de déploiement**
que cette tâche — analyse de code statique, en lecture seule — ne peut pas
produire. Elles sont donc marquées **❓** ci-dessous, avec ce qui *serait*
nécessaire pour les mesurer, plutôt qu'une estimation devinée :

- **Fréquence de déploiement** — ❓ Non mesurable depuis le code. La CI
  publie une image à chaque push sur `main`/`develop` (`ci.yml:677`,
  potentiellement plusieurs fois par jour), mais rien ne garantit qu'un
  déploiement suit — `cd-prod.yml` (backend) exige encore une
  infrastructure qui, selon son propre commentaire, **n'existe pas au
  31 août 2026** (`cd-prod.yml:27-35`). La fréquence de *publication d'image*
  n'est pas la fréquence de *déploiement*.
- **Lead time for changes** — ❓ Non mesurable sans historique des
  déploiements réels. Un proxy partiel côté CI : le filtrage par chemin
  (`ci.yml:43-106`, job `changes`) réduit le temps d'exécution en ne
  lançant que les jobs du service modifié — un vrai levier de lead time,
  mais seulement sur la portion « CI verte », pas sur le chemin complet
  jusqu'en production.
- **Taux d'échec des changements** — ❓ Nécessiterait un historique
  d'incidents de production corrélé aux déploiements ; hors périmètre de
  cette lecture de code. `docs/ETAT_DU_SYSTEME.md` (registre d'anomalies
  ANO-XXX déjà tenu par le projet) serait la source à interroger pour une
  vraie mesure — non fait ici, volontairement, pour ne pas sortir du
  périmètre CI de cette tâche.
- **MTTR (Mean Time to Restore)** — ❓ Non mesurable. Point positif
  observé côté conception (pas une mesure de MTTR réel) : `cd-prod.yml:
  254-287` documente une procédure de rollback automatique claire en cas
  d'échec de déploiement, ce qui *devrait* réduire un MTTR réel si
  déclenché — mais c'est une capacité, pas une métrique observée.

**Conclusion DORA** : ces métriques ne peuvent honnêtement recevoir un
verdict ✅/🟡/❌ depuis une analyse de code seule — les marquer autrement
serait deviner. Ce que la CI *permet* (builds rapides et sélectifs,
rollback scripté) est un bon terreau pour un lead time court et un MTTR
faible, mais seule une instrumentation réelle (voir `CLAUDE.md` §
Observabilité : « AUCUNE aujourd'hui ») permettrait de le vérifier.

---

## 3. Plan de remédiation priorisé

| # | Écart | Référentiel | Effort | Recommandation |
|---|---|---|---|---|
| 1 | Image scannée ≠ image publiée/déployée (backend : `publish-*` ne dépend pas de `docker-build-*` ; frontend : image déployée jamais scannée) | OWASP CICD-SEC-9, CIS 2.4.4 | **M** | Faire dépendre chaque `publish-*` de son `docker-build-*` (backend) ; ajouter Trivy dans `cd-canary.yml`/`cd-staging.yml` avant `push` (frontend). Idéalement, unifier en un seul build par service. |
| 2 | 0 revue requise sur `develop` (backend), aucune protection de branche (`main` backend, `develop`+`main` frontend) | OWASP CICD-SEC-1, SLSA Source L2/L4, CIS 1.1.3/1.1.9/1.1.11/1.1.14/1.1.16/1.1.17 | **S** | Backend : ajouter `required_status_checks` sur `main` (actuellement absent), `required_conversation_resolution:true`. Frontend : exécuter une version adaptée de `setup-branch-protection.sh` avec les vrais noms de jobs. Traiter la question de la revue à 2 personnes séparément (dépend d'un second collaborateur). |
| 3 | `release.yml` non épinglé par SHA, `@master` sur une action tierce | OWASP CICD-SEC-3, guide GitHub (pinning) | **S** | Épingler les 3 actions par SHA ; remplacer `@master` par un tag de release fixe de `commitizen-tools/commitizen-action`. |
| 4 | `sha_pinning_required:false` et `allowed_actions:"all"` sur les deux dépôts | OWASP CICD-SEC-3/CICD-SEC-8, guide GitHub | **S** | Activer `sha_pinning_required:true` ; passer `allowed_actions` à `selected` avec la liste des actions déjà utilisées. |
| 5 | Dependabot security updates désactivé sur les deux dépôts (repos publics) | NIST RV.1, CIS 3.2.2 | **S** | Activer le réglage `dependabot_security_updates` dans les paramètres de sécurité des deux dépôts (indépendant du fichier `dependabot.yml`). |
| 6 | Pas de `CODEOWNERS` (backend), pas de `SECURITY.md` (les deux, dépôts publics) | CIS 1.1.6/1.1.7/1.2.1 | **S** | Ajouter un `CODEOWNERS` backend (calqué sur celui du frontend) ; ajouter un `SECURITY.md` minimal aux deux dépôts. |
| 7 | Aucun `timeout-minutes` sur 13 jobs directs de `ci.yml` (backend) | Guide GitHub / CIS (hygiène) | **S** | Ajouter `timeout-minutes` (10-20 min selon le job) sur chaque job direct. |
| 8 | Pas de bloc `permissions:` explicite dans `ci.yml` (frontend) | Guide GitHub, OWASP CICD-SEC-7 | **S** | Ajouter `permissions: contents: read` en tête de fichier, ne pas dépendre uniquement du réglage de dépôt. |
| 9 | Aucun SAST sécurité côté frontend (pas de `bandit`-équivalent, pas d'ESLint sécurité) ; `bandit` seul (limité) côté backend | NIST PW.7 | **M** | Frontend : ajouter `eslint-plugin-security` ou Semgrep au job `quality`. Backend : envisager Semgrep/CodeQL en complément de `bandit`. |
| 10 | Aucun `npm audit`/Dependabot npm côté frontend | NIST PW.4, CIS 3.2.2 | **S** | Ajouter `npm audit --audit-level=high` à `ci.yml` ; créer `dependabot.yml` frontend (npm + github-actions). |
| 11 | Pas de génération SBOM/provenance explicite côté frontend | SLSA Build L1, NIST PS.3.2 | **S** | Ajouter `sbom: true`/`provenance: true` sur `docker/build-push-action` dans `cd-canary.yml`/`cd-staging.yml`. |
| 12 | Signature/provenance générées dans le même job que le build (backend) — SLSA Build L3 non atteint | SLSA Build L3 | **L** | Migrer vers `slsa-framework/slsa-github-generator` pour isoler la génération de provenance du build applicatif. |
| 13 | Aucun scanner de mauvaise configuration de pipeline (`actionlint`/`zizmor`) sur les deux dépôts | CIS 2.3.6, OWASP CICD-SEC-8 | **S** | Ajouter `actionlint` (ou `zizmor`) en étape de CI sur les deux dépôts. |
| 14 | Environnement `production` (backend, `cd-prod.yml`) et `production-canary`/`production-canary-gate` (frontend) sans règles de protection réelles | Guide GitHub (environnements protégés) — *point CD, noté pour mémoire* | **M** | Créer ces environnements dans les réglages du dépôt avec de vrais reviewers requis, pas seulement une déclaration `environment:` dans le YAML. |

---

## 4. Sources citées

- OWASP — [Top 10 CI/CD Security Risks](https://owasp.org/www-project-top-10-ci-cd-security-risks/) — projet stable depuis octobre 2022, consulté le 4 septembre 2026, aucune v2 publiée à ce jour.
- SLSA — [slsa.dev](https://slsa.dev/), spécification **v1.2** publiée le 24 novembre 2025 : [annonce v1.2](https://slsa.dev/blog/2025/11/announce-slsa-v1.2), [Build Track — niveaux](https://slsa.dev/spec/v1.0/levels), [Source Track — exigences](https://slsa.dev/spec/v1.2/source-requirements).
- NIST — [SP 800-218, Secure Software Development Framework (SSDF) v1.1](https://nvlpubs.nist.gov/nistpubs/SpecialPublications/NIST.SP.800-218.pdf) ; [page projet CSRC](https://csrc.nist.gov/projects/ssdf).
- CIS — [Software Supply Chain Security Guide v1.0, juin 2022](https://www.cisecurity.org/insights/white-papers/cis-software-supply-chain-security-guide) (texte intégral consulté via la copie hébergée par le projet `aquasecurity/chain-bench`).
- GitHub — [Security hardening for GitHub Actions](https://docs.github.com/en/actions/security-for-github-actions/security-guides/security-hardening-for-github-actions) (documentation vivante, consultée le 4 septembre 2026) ; [changelog — SHA pinning enforcement, 15 août 2025](https://github.blog/changelog/2025-08-15-github-actions-policy-now-supports-blocking-and-sha-pinning-actions/).
- DORA / Google Cloud — [2025 DORA Report: State of AI-assisted Software Development](https://cloud.google.com/blog/products/ai-machine-learning/announcing-the-2025-dora-report) ; [Four Keys — mesurer la performance DevOps](https://cloud.google.com/blog/products/devops-sre/using-the-four-keys-to-measure-your-devops-performance).

**Preuves internes** (API GitHub, exécutées le 4 septembre 2026, sorties
brutes conservées dans l'historique de cette tâche) :
`gh api repos/SteveElouga/SGFE-Backend/branches/{main,develop}/protection`,
`gh api repos/SteveElouga/SGFE-Frontend/branches/{main,develop}/protection`,
`gh api repos/SteveElouga/SGFE-{Backend,Frontend}/actions/permissions`,
`gh api repos/SteveElouga/SGFE-{Backend,Frontend}/actions/permissions/workflow`,
`gh api repos/SteveElouga/SGFE-{Backend,Frontend} --jq '.security_and_analysis'`,
`gh api repos/SteveElouga/SGFE-{Backend,Frontend}/environments`,
`gh api repos/SteveElouga/SGFE-Backend/collaborators`.

**Fichiers lus intégralement** (backend) : `.github/workflows/ci.yml`,
`.github/workflows/cd-prod.yml`, `.github/workflows/release.yml`,
`.github/workflows/_build-scan.yml`, `.github/workflows/_publish-image.yml`,
`.github/dependabot.yml`, `scripts/sync-grpc-lib.sh`,
`scripts/gen-jwt-keys.sh`, `scripts/setup-branch-protection.sh`.
**Fichiers lus intégralement** (frontend) : `.github/workflows/ci.yml`,
`.github/workflows/cd-canary.yml`, `.github/workflows/cd-staging.yml`,
`.github/workflows/rollback.yml`, `.github/CODEOWNERS`.
`AUDIT_SGFE.md` (les deux dépôts) a été lu pour contexte, sans reprendre
aucune de ses affirmations sans re-vérification directe dans le YAML —
conformément à la consigne de cette tâche.
