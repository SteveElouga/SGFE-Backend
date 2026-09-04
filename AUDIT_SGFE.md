# Bilan d'audit technique — Système de Gestion de Facturation d'Eau (SGFE)

**Périmètre audité :** dépôts `SGFE-backend` (microservices Django + gRPC, gateway GraphQL, service WhatsApp Node.js) et `SGFE-frontend` (Angular 22 PWA).
**Date :** 17 juillet 2026
**Méthode :** analyse du **code réellement livré** (368 fichiers Python, 133 fichiers TypeScript, 8 fichiers `.proto`, configs Docker/CI). La documentation interne (`CLAUDE.md`, `docs/`) décrit une architecture *cible* qui diverge parfois du code ; toutes les conclusions ci‑dessous s'appuient sur le code réel, avec références `fichier:ligne` vérifiables.
**Public :** ce document combine une **synthèse pour décideurs** (sections 1‑2) et un **détail technique actionnable** (sections 3‑8).

---

> ⚠️ **Revue de fraîcheur — 3 septembre 2026.** Cet audit datait du 17 juillet ;
> plus de 100 PR ont été mergées depuis sur les deux dépôts (backend #94→#167,
> frontend #43→#141). Deux agents ont revérifié **le code réel**, item par
> item, contre la checklist §8 — sans se fier aux seuls titres de PR (une
> vérification a d'ailleurs révélé qu'une PR intitulée « conception identité
> → journal d'audit » n'est qu'un **document**, pas une implémentation : le
> journal d'audit reste totalement absent du code, voir §10.7 déjà correct
> sur ce point).
>
> **Décompte mis à jour (94 items, §8)** : **37 faits** (39 %) · **11
> partiels** · **34 non faits** · **12 non revérifiés/incertains dans cette
> passe**. Détail par priorité dans le tableau de décompte en fin de §8.
>
> **Ce qui a bougé le plus** : la quasi-totalité du **P0 sécurité/config**
> (secrets fail-fast, JWT RS256, isolation réseau, authentification gRPC
> interne, rate limiting, durcissement GraphQL, en-têtes de sécurité) a été
> livrée en une semaine, **19-23 juillet — juste après la rédaction de cet
> audit**. Le bug SUPERVISEUR (§8·D) était même déjà en cours de correction
> *pendant* l'audit. Le **volet financier correctif** (§8·G — avoir,
> remboursement, reçu PDF, trop-perçu) et l'**espace abonné** (§8·H) sont
> désormais complets des deux côtés. Côté frontend, une refonte massive
> (27 août, PR #62-#64 + #65-#73) a traité une bonne partie du P2 : `strict`
> TypeScript, `graphql-codegen`, e2e Playwright réels, CHÈQUE retiré.
>
> **Ce qui reste ouvert et compte le plus** : **observabilité (§8·I) et piste
> d'audit (§8·J) sont à zéro** — aucun `TracerProvider`, aucun `/metrics`,
> aucun journal d'audit, malgré plusieurs mentions déclaratives ; **mTLS
> inter-services toujours absent** (§8·B) ; **pagination serveur toujours
> absente** (§8·L/M — les listes s'affichent paginées mais tout part sur le
> réseau en un seul appel) ; **4 parcours frontend identifiés comme critiques
> (file offline terrain, refresh+retry, guards de rôle, interceptor JWT) ont
> toujours zéro test dédié**, malgré le doublement du volume de tests global.
> Une **régression relative** notable : l'authentification gRPC interne
> (livrée, bien) a dupliqué son code **8 fois** au lieu d'une lib partagée —
> le point §8·L sur `sgfe_common.grpc` est donc *plus* justifié qu'en juillet.
>
> **Déploiement (§10) : la cible a changé d'Azure vers AWS le 28 août 2026**
> — voir l'encart déjà en tête de §10, qui reste la référence à jour
> (`docs/INFRASTRUCTURE_AWS.md`, `docs/CHAINE_DE_LIVRAISON.md`).
>
> **Périmètre de cette revue** : la checklist §8 (ci-dessous) a été
> intégralement réannotée avec preuve (fichier:ligne ou PR+date) pour chaque
> item vérifiable. Les sections narratives 1‑7 et 9 n'ont **pas** été
> réécrites — leurs constats qualitatifs (architecture, rigueur financière,
> patterns de résilience) restent globalement valables et n'ont pas été
> recontrôlés phrase par phrase dans cette passe ; seule la checklist
> actionnable a été mise à jour car c'est elle qui sert de tableau de bord.

> ⚠️ **3e revue de fraîcheur — 3 septembre 2026 (soir), après fusion complète.**
> La revue ci-dessus (2e passe, même journée) comptait « ✅ Fait » des PR
> encore ouvertes. **Toutes ont depuis été fusionnées dans `develop`**
> (backend #168-174, frontend #142-144), et **8 PR supplémentaires** ouvertes
> et fusionnées le même jour : dead-letter Redis Streams sur un incident réel
> découvert en rédigeant le runbook (#177), runbook d'exploitation (#176),
> healthchecks complets sur les 9 composants (#175), plan de reprise
> d'activité avec un vrai écart signalé — sauvegardes jamais envoyées vers le
> bucket S3 provisionné (#178), export/anonymisation RGPD (#179), correctif
> d'une régression du proxy de dev local causée par le durcissement TLS
> (#180 backend + #146 frontend), consommation de la pagination côté UI
> (#145 frontend), et `mypy --strict` porté à 6 composants sur 9 (#181).
>
> **Décompte final (96 items, §8 — le total est passé de 94 à 96 : les deux
> incidents/régressions découverts et corrigés ce jour comptent comme des
> items propres)** : **72 faits (75 %)** · **2 partiels** · **7 incertains/non
> revérifiés** · **15 non faits**. Détail dans le tableau de décompte en fin
> de §8, refait à neuf.
>
> **Seul vrai blocage de production inchangé** : l'observabilité (§I, hors
> healthchecks désormais complets) et la piste d'audit (§J) n'ont **jamais
> été entamées** — chantiers à part entière, jamais planifiés dans aucune des
> trois passes du 3 septembre. C'est le verrou SOC 2 et la porte « Go
> production » restants, sans ambiguïté.

## 1. Synthèse exécutive

Le SGFE est un projet d'une **maturité technique remarquable pour son stade** : architecture microservices propre et cohérente, backend rigoureux (gestion monétaire en `Decimal`, transactions, verrous de concurrence, règles métier codées en défense en profondeur), frontend Angular ultra‑moderne (zoneless, signals, offline‑first sur l'interface terrain), et une chaîne d'intégration continue de niveau professionnel (SAST, scan de dépendances, gate de couverture 80 %, SBOM + signature d'images). Le contrat d'API entre le front et le back est **aligné à un niveau inhabituel** (61 opérations GraphQL concordantes).

Cependant, l'audit du code réel révèle un **écart net entre la qualité de conception « au repos » et la robustesse « en fonctionnement distribué / production »**. Le système se comporte comme un **excellent prototype avancé (MVP+)**, pas encore comme un produit prêt pour la production, pour quatre raisons principales : (1) la sécurité repose entièrement sur un seul point de contrôle (la gateway) tandis que la couche interne gRPC est **en clair et non authentifiée** ; (2) il n'existe **aucune cible de déploiement backend, aucune observabilité réelle, aucune sauvegarde** ; (3) quelques **bugs concrets** cassent des parcours (rôle SUPERVISEUR, mode de paiement CHÈQUE) ; (4) il manque tout le volet **financier correctif** (avoir, annulation/remboursement de paiement, reçu) et l'**espace abonné public** n'est qu'une coquille côté frontend.

### Tableau de notation par axe

| Axe | Note /5 | Commentaire d'une ligne |
|---|:---:|---|
| Architecture & conception | ★★★★★ | Microservices + gRPC + gateway GraphQL, séparation stricte, très cohérent |
| Qualité du code backend | ★★★★½ | Layering respecté, `Decimal` partout, règles métier solides ; dette = robustesse distribuée |
| Qualité du code frontend | ★★★★ | Angular 22 moderne et propre ; freins = tests ~10 %, `strict` TS off |
| Sécurité (code réel) | ★★ | Excellente couche applicative, mais gRPC en clair + secrets par défaut + 0 rate limiting |
| Conformité SOC 2 | ✗ | **Non prêt** pour un Type II (écarts bloquants CC6/CC7/CC8) |
| Alignement front/back | ★★★★ | Contrat GraphQL excellent ; 2 défauts réels (SUPERVISEUR, CHÈQUE) |
| Complétude fonctionnelle | ★★★½ | Cycle nominal complet ; manque le correctif financier + portail abonné |
| Tests backend | ★★★★ | ~80‑88 %, imposé par la CI |
| Tests frontend | ★½ | ~10 %, la logique critique (file offline) non testée |
| DevOps — « build » (CI, images) | ★★★★½ | Dockerfiles + CI supply‑chain exemplaires |
| DevOps — « run » (déploiement, obs., HA, backups) | ★½ | Déploiement backend absent, observabilité fictive, SPOF multiples |

### Verdicts clés

*(verdicts d'origine, 17 juillet — voir la mise à jour au 3 septembre juste en dessous)*

- **Prêt pour la production ?** → **Non**, pas en l'état. Fondations logicielles excellentes, mais la moitié « exploitation » manque (déploiement backend, observabilité, sauvegardes, redondance) et un secret tiers réel est exposé.
- **Conforme SOC 2 ?** → **Non.** La culture sécurité est réelle (auth soignée, CI durcie) mais les contrôles d'accès inter‑services, la gestion des secrets et surtout la **piste d'audit / journalisation** — indispensable à un Type II — sont absents.
- **Le front et le back fonctionnent‑ils ensemble ?** → **Oui pour ADMIN/AGENT/COMPTABLE.** Le rôle **SUPERVISEUR est cassé** par un bug backend trivial à corriger.

> **Mise à jour — 3 septembre 2026 :**
> - **Prêt pour la production ?** → **Toujours non, mais nettement plus proche.** Le déploiement backend existe désormais (AWS/Ansible, CD complet), les sauvegardes tournent, la sécurité applicative P0 est faite à 19/27. Ce qui bloque encore un Go réel : **mTLS**, le **restore drill** et le **chiffrement des sauvegardes** non confirmés, et l'**observabilité (§I) toujours à zéro** — voir la porte « Go production » en §8.
> - **Conforme SOC 2 ?** → **Toujours non**, sans ambiguïté : le **journal d'audit reste totalement absent du code** (une PR au titre trompeur n'a livré qu'un document de conception, §10.7) et l'observabilité n'a pas progressé. C'est le vrai verrou SOC 2, pas la sécurité applicative (qui, elle, a bien avancé).
> - **Le front et le back fonctionnent‑ils ensemble ?** → **Oui, et le bug SUPERVISEUR est corrigé** (PR #95/#96, 19 juillet — il était déjà en cours de correction pendant la rédaction de cet audit).

### Top 5 des risques à traiter en priorité

*(risques d'origine, 17 juillet — 4 des 5 sont résolus, voir la liste actualisée juste en dessous)*

1. ~~Clé API Brevo réelle (live) en clair~~ — **❓ statut de la rotation elle-même incertain** (le fail-fast qui empêche une valeur par défaut vide est fait, §8·A ; la rotation de la clé déjà exposée n'est pas vérifiable depuis le code).
2. ~~gRPC inter‑services en clair et non authentifié~~ — **✅ authentification applicative faite** (jeton d'identité + intercepteur, PR #138) ; **le chiffrement transport (mTLS) reste ouvert**, voir risque actualisé #1.
3. ~~Aucun déploiement backend ni sauvegarde~~ — **✅ fait et dépassé** (cible AWS complète, CD, sauvegardes automatiques).
4. ~~Rôle SUPERVISEUR non fonctionnel~~ — **✅ corrigé** (PR #95/#96).
5. ~~Pas de journal d'audit ni d'observabilité~~ — **toujours vrai, inchangé : c'est le risque n°1 actualisé.**

**Top 5 actualisé (3 septembre 2026) :**

1. **Observabilité et piste d'audit toujours à zéro** (§8·I, §8·J) : aucun `TracerProvider`, aucun `/metrics`, aucun journal d'audit — impossible de diagnostiquer un incident ou de prouver « qui a fait quoi ». Seul vrai blocage SOC 2 restant. **Toujours hors périmètre — non traité intentionnellement (chantier à part entière, décision actée le 3 septembre).**
2. ~~mTLS inter‑services toujours absent~~ — **✅ Traité et vérifié en direct, PR #168 (fusionnée 03/09).** Chiffrement + authentification mutuelle du transport gRPC sur les 9 composants, repli en clair pour les tests. Vérification live faite le 03/09 (soir) : stack de 21 conteneurs entièrement reconstruite et relancée, tous les services `healthy`.
3. ~~Sauvegardes non éprouvées~~ — **✅ Traité, PR #169 (fusionnée 03/09).** Chiffrement AES-256 + restore drill réellement exécuté (4 tables restaurées et vérifiées sur un Postgres jetable).
4. ~~4 parcours frontend critiques toujours sans test~~ — **✅ Traité, PR #142 (fusionnée 31/08).** 40 nouveaux tests (241→281). Deux constats de conception documentés (LIFO vs FIFO sur la file hors-ligne, refresh REST non mutualisé) plutôt que corrigés, hors périmètre.
5. **Pagination serveur absente** (§8·L/M) — **toujours vrai, inchangé.** `GET_ABONNES` et consorts n'ont ni `limit` ni `offset` — toute liste part intégralement sur le réseau à chaque appel ; ne passera pas à l'échelle au-delà de quelques centaines d'abonnés. Non traité dans ce tour (classé P2 « Important », explicitement laissé de côté avec l'observabilité).

**Nouveau Top 5 (une fois #2/#3/#4 ci-dessus fusionnés)** — ce qui restera vraiment à traiter :

1. Observabilité + piste d'audit (toujours à zéro, exclu par décision).
2. Pagination serveur absente (P2, exclu par décision).
3. **Rotation de la clé Brevo non confirmée** (§8·A) — action manuelle hors code, jamais vérifiable depuis le dépôt. Seul item P0 non fermé.
4. **Trivy sur l'image frontend va faire échouer la CI dès son premier run** (§8·K) — découvert par la PR #143 : 2 CRITICAL + 35 HIGH dans `nginx:1.27-alpine` (image de base non reconstruite depuis avril 2025, pas le code applicatif) — un bump de tag suffit, mais c'est un blocage CI immédiat à anticiper.

~~`docker-compose.yml` code encore les secrets en dur~~ — **✅ traité le 3 septembre (soir)**, même PR #169 : interpolation `${VAR}` ajoutée, comportement local inchangé, effet réel du provisioning désormais sur tous les services.

---

## 2. Points forts et points faibles (Q1)

### 2.1 Points forts

**Architecture.** Le principe « un service = un projet Django + une base PostgreSQL dédiée, communication exclusivement par gRPC » est réellement respecté (8 bases distinctes, `docker-compose.yml`). La gateway GraphQL isole le frontend des microservices. Le découpage en 9 composants (auth, abonné, campagne, facturation, paiement, notification, reporting, config, gateway) est net et le pattern interne (`models` / `repositories` / `services` / `serializers` / `grpc_server` / `grpc_clients`) est appliqué **uniformément** sur tous les services.

**Rigueur financière (essentielle pour un facturier).** Tous les montants sont en `DecimalField`, jamais en `float`. Le calcul de facture est exact et arrondi explicitement : `montant = (consommation * prix_m3).quantize(Decimal("0.01"), ROUND_HALF_UP)` (`services/facturation/factures/services.py:172`), le `prix_m3` est **copié** dans la facture (jamais une FK vers le tarif, `services.py:191`), et les valeurs transitant en `double` sur gRPC sont reconverties via l'idiome sûr `Decimal(str(x))` à chaque entrée (`services/paiement/paiements/services.py:49,80,100`).

**Robustesse métier & concurrence.** `transaction.atomic()` à tous les points multi‑écritures ; `SELECT … FOR UPDATE` pour sérialiser les versements concurrents et la numérotation des factures (`paiement/repositories.py:86`, `facturation/services.py:183`) ; validations en défense en profondeur (index ≥ ancien vérifié à la saisie **et** re‑vérifié à la facturation, abonné ACTIF exigé, référence obligatoire pour Mobile Money/Virement, anti‑surpaiement) ; consumer d'événements reporting **idempotent** via Redis Streams + déduplication `ProcessedEvent`.

**Frontend moderne et soigné.** Angular 22 **zoneless** (API stable `provideZonelessChangeDetection()`), 100 % standalone + OnPush (63/63 composants), **signals** massivement utilisés (470 usages), access token **en mémoire** (pas de `localStorage` → surface XSS réduite) avec **refresh silencieux** via cookie HttpOnly, gestion d'erreurs GraphQL centralisée, i18n réel (fr + en, 1086 lignes chacun). L'**interface terrain** est le joyau du projet : réellement offline‑first (file persistée, auto‑sync au retour réseau, UI optimiste), mobile‑first et conforme au « 3 taps ».

**Ingénierie logicielle.** Dépendances **entièrement figées** (`Django==5.2.15`, etc.), `USE_TZ=True` sans date naïve, **0 `Any`** et **0 `TODO/FIXME`** côté backend, CI backend qui **impose** `coverage --fail-under=80` par service, Dockerfiles multi‑stage à digest SHA épinglé + non‑root, chaîne supply‑chain (SBOM, provenance, signature cosign), pre‑commit (ruff), Dependabot, gitleaks, Bandit, `pip-audit`, Trivy.

### 2.2 Points faibles / risques — vue priorisée

| Priorité | Faiblesse | Nature | Réf. |
|:---:|---|---|---|
| 🔴 P0 | Secret Brevo **réel** + mot de passe admin en clair sur disque | Sécurité / secrets | `services/auth/.env:31,43` |
| 🔴 P0 | gRPC inter‑services **en clair, non authentifié**, RBAC concentré à la gateway | Sécurité / archi | 34× `insecure_port/channel` |
| 🔴 P0 | Configuration **insecure‑by‑default** (`DEBUG=True`, `SECRET_KEY` par défaut, JWT HS256 à secret faible partagé) | Sécurité / config | `services/auth/auth/settings.py:15‑16,99` |
| 🔴 P0 | **Aucun déploiement backend, aucune sauvegarde**, SPOF multiples | Prod‑readiness | `backend/.github/workflows/` |
| 🔴 P0 | Rôle **SUPERVISEUR cassé** (`AttributeError`) | Bug fonctionnel | `gateway/schema/campagne_queries.py:32` |
| 🟠 P1 | **0 rate limiting** (login, OTP, reset → brute‑force / bombing) | Sécurité | recherche `throttle` = 0 |
| 🟠 P1 | Introspection/GraphiQL en prod, **pas de limite de profondeur/complexité** GraphQL | Sécurité | `gateway/gateway/urls.py:11` |
| 🟠 P1 | **Aucune piste d'audit** métier ni logs structurés ni observabilité | SOC2 / exploitation | 0 `LOGGING`, 0 usage OTel |
| 🟠 P1 | **Facture orpheline non payable** si Paiement indisponible à la génération (dual‑write best‑effort non réconcilié) | Robustesse distribuée | `facturation/grpc_clients.py:246`, `services.py:209` |
| 🟠 P1 | Mode de paiement **CHÈQUE** offert par le front, absent de l'enum backend | Alignement | `paiement/models.py:9‑11` |
| 🟠 P1 | **Espace abonné** = coquille vide côté frontend | Fonctionnel | `espace-abonne.component.ts:54` |
| 🟠 P1 | Couverture de tests frontend **~10 %** (file offline critique non testée) ; e2e non fonctionnel | Qualité | 13 `*.spec.ts` / ~130 unités |
| 🟡 P2 | Crons in‑process **sans verrou** → doublons sous canary/multi‑réplicas | Robustesse | `paiement/schedulers.py:45` |
| 🟡 P2 | `EnregistrerPaiement` **non idempotent** (double soumission) | Robustesse | `paiement/grpc_server.py:55` |
| 🟡 P2 | `strict` TypeScript **désactivé** côté front | Qualité | `tsconfig.json:5‑21` |
| 🟡 P2 | Incohérence de type sur l'index compteur (`Float` campagne vs `Decimal` ailleurs) | Qualité | `campagne/models.py:69` |
| 🟡 P2 | PII abonné (nom, téléphone, adresse) **en clair au repos** | Confidentialité | `abonne/abonnes/models.py:23` |

---

## 3. Sécurité et conformité SOC 2 (Q2)

**Posture globale.** La couche applicative *visible* (auth + gateway) est étonnamment mûre. Mais toute cette sécurité repose sur **un seul point de contrôle — la gateway** — alors que la couche de transport interne est totalement ouverte, et que la configuration est « insecure‑by‑default ». Un attaquant disposant du moindre accès au réseau interne, ou un déploiement qui hérite d'une valeur par défaut, contourne l'intégralité des contrôles.

### 3.1 Constats critiques (détaillés)

**C1 — gRPC inter‑services en clair et non authentifié (le talon d'Achille).**
Les 8 services exposent leurs RPC via `server.add_insecure_port(...)` et sont appelés via `grpc.insecure_channel(...)` (34 occurrences ; **aucun** mTLS). Le seul intercepteur serveur est un `ErrorHandlingInterceptor` — il ne vérifie **ni token, ni rôle, ni métadonnée**. Tout le RBAC est uniquement dans `gateway/schema/*` (`require_role`) ; les services font une confiance aveugle à la gateway. Concrètement, quiconque atteint `auth-service:50051` peut appeler `CreateUser(role="ADMIN")` ou `DeactivateUser` ; `paiement-service:50055` → solder frauduleusement des factures ; `abonne-service:50052` → lire toute la base abonnés — **sans aucune authentification**. En `docker-compose.yml`, ces ports (50051‑50058) sont même publiés sur l'hôte. *(SOC 2 : CC6.1, CC6.6 — bloquant.)*

**C2 — Configuration insecure‑by‑default + forge de JWT.**
`services/auth/auth/settings.py:15‑16` : `SECRET_KEY` par défaut `"django-insecure-dev-key-change-me"` et `DEBUG` par défaut `True`. Le JWT est signé en **HS256** avec `JWT_SECRET_KEY` dont la valeur par défaut est cette `SECRET_KEY` publique (`settings.py:99`). Comme la validation ne fait que vérifier signature + blacklist, connaître ce secret permet de **forger un access token `role="ADMIN"`** accepté par toute la plateforme. Rien n'empêche techniquement un déploiement de tourner avec ces défauts. *(CC6.1, CC8.1.)*

**C3 — Secret tiers réel exposé.**
`services/auth/.env` contient une **clé API Brevo active** (format `xkeysib-ae6d…`) et `ADMIN_PASSWORD=Admin1234!` ; la racine contient `WHATSAPP_INTERNAL_API_KEY`. Le `.gitignore` est **correctement configuré** (`.env`, `services/*/.env` exclus) — c'est un bon point — mais ces secrets sont **matérialisés sur disque** et la clé Brevo est une **credential tierce vivante** qu'il faut révoquer. *(CC6.1.)*

### 3.2 Tableau des constats (du plus grave au moins grave)

| Sévérité | Constat | Réf. | Critère SOC 2 |
|---|---|---|---|
| 🔴 Critique | gRPC inter‑services en clair + non authentifié ; RBAC uniquement à la gateway | 34× `insecure_*` ; `services/*/…/grpc_interceptors.py` | CC6.1, CC6.6 |
| 🔴 Critique | `DEBUG=True`, `SECRET_KEY`/`JWT_SECRET_KEY` par défaut → forge de JWT admin | `services/auth/auth/settings.py:15‑16,99,116` | CC6.1, CC8.1 |
| 🟠 Élevé | Clé Brevo live + mot de passe admin + secrets DB en clair (compose code en dur `JWT_SECRET_KEY`, `POSTGRES_PASSWORD`) | `services/auth/.env:31,43` ; `docker-compose.yml:62,71` | CC6.1 |
| 🟠 Élevé | Aucun rate limiting (login verrouillé par compte seulement ; OTP/reset non throttlés → bombing WhatsApp/e‑mail) | recherche `throttle/limit_req` = 0 | CC6.1, CC7.2 |
| 🟠 Élevé | Introspection + GraphiQL activés en prod ; aucune limite de profondeur/complexité/coût | `gateway/gateway/urls.py:11` ; `gateway/schema/schema.py:8` | CC6.1, A1.1 |
| 🟡 Moyen | Access token JWT valable **24 h** (fenêtre de vol longue) | `services/auth/auth/settings.py:101` | CC6.1 |
| 🟡 Moyen | En‑têtes de sécurité manquants (pas de HSTS/CSP, pas de `SecurityMiddleware` sur la gateway) | `gateway/gateway/settings.py:21` ; `nginx-lb.conf.tpl:56` | CC6.1 |
| 🟡 Moyen | PII abonné en clair au repos (nom, téléphone WhatsApp, adresse) | `services/abonne/abonnes/models.py:23‑26` | C1.1 |
| 🟡 Moyen | whatsapp‑service **fail‑open** : si la clé est vide, `/send`, `/send-with-pdf`, `/qr` deviennent publics | `whatsapp-service/server.js:35` | CC6.1 |
| 🟡 Moyen | **Aucun journal d'audit** ni logs de sécurité ; OpenTelemetry/Prometheus déclarés mais **jamais câblés** (0 usage en code) | 0 `LOGGING=`, 0 usage OTel dans `.py` | CC7.2, CC7.3 |
| 🟢 Faible | Cookie refresh `Secure = not DEBUG` → non‑Secure si un déploiement laisse `DEBUG=True` | `gateway/gateway/settings.py:84` | CC6.1 |
| 🟢 Faible | Token espace‑abonné dans l'URL (apparaît dans les logs d'accès) | `notifications/models.py:65` | C1.1 |

### 3.3 Points forts sécurité (à préserver)

Anti‑énumération au login (hash factice + message générique, `comptes/services.py:52`), **verrouillage de compte** anti‑bruteforce (5 essais / 15 min), **rotation + blacklist des refresh tokens**, OTP **haché** au repos + plafond d'essais, tokens d'activation/reset robustes (`secrets.token_urlsafe(32)`, usage unique, TTL), **protection IDOR** de l'espace abonné (le PDF vérifie `facture.abonne_id == token.abonne_id`, `espace_abonne.py:120`), RBAC cohérent + contrôle de propriété (SUPERVISEUR restreint à ses campagnes, AGENT à sa tournée), **ORM pur → pas d'injection SQL**, et une CI qui exécute Bandit + `pip-audit` + gitleaks + gate de couverture.

### 3.4 Verdict SOC 2 — prêt pour un Type II ?

**Non.** Un audit Type II teste l'**efficacité opérationnelle dans la durée**. Écarts bloquants :

1. **CC6.1 / CC6.6 — contrôle d'accès logique absent au niveau des services.** Une seule défaillance de périmètre réseau = compromission totale.
2. **CC6.1 / CC8.1 — secrets & configuration** (défauts insecure, JWT symétrique faible, clé Brevo live à rotationner).
3. **CC7.2 / CC7.3 — surveillance & piste d'audit inexistantes.** Sans preuve « qui a fait quoi quand » ni détection d'incident, le critère ne peut être satisfait — et c'est le plus difficile à rattraper *rétroactivement*, car un Type II exige une **période d'observation** avec des preuves accumulées. **À démarrer en premier.**
4. **CC6.1 / A1.1 — absence de rate limiting** et **GraphQL non durci**.

Une fois ces chantiers menés, la base applicative existante (auth, RBAC, intégrité de traitement, CI) constitue un socle crédible pour viser un Type II après la période d'observation requise.

---

## 4. Alignement Frontend / Backend (Q3)

**Degré d'alignement : élevé.** Les **61 opérations** GraphQL appelées par le front (queries, mutations, subscriptions) existent toutes côté gateway, avec les mêmes noms, arguments et inputs ; la conversion `snake_case` (Python/proto) → `camelCase` (GraphQL) est cohérente ; les enums de statut (facture `IMPAYEE/PARTIELLE/PAYEE`, abonné `ACTIF/SUSPENDU/RESILIE`) et le **modèle à 4 rôles** concordent des deux côtés ; les endpoints REST (PDF facture, CSV factures/paiements, PDF synthèse, bilan impayés) sont **implémentés, appelés ET proxyfiés**.

### 4.1 Ce qui est aligné (échantillon)

`login/refreshToken/logout/me`, `saisirIndex(SaisirIndexInput!)` (chaîne proto → service → gateway → front cohérente), `enregistrerPaiement` (référence obligatoire Mobile Money/Virement des deux côtés), `genererFactures`, `factures/facture/facturesParCampagne` avec champs enrichis, `abonnes/createAbonne/updateCompteur/remplacerCompteur`, `statsGlobales`, `configs/updateConfig/infosSociete/whatsappStatus`, subscriptions temps réel. Aucune opération fantôme ni champ inexistant dans les types.

### 4.2 Désalignements réels

| Gravité | Sujet | Front | Back | Impact |
|---|---|---|---|---|
| 🟠 Élevée | **Rôle SUPERVISEUR** | Câblé de bout en bout (landing `/campagnes`, routes, création d'utilisateur SUPERVISEUR) | `_verifier_acces_campagne` lit `campagne.created_by` (`campagne_queries.py:32`) mais **`CampagneResponse` du proto n'a pas ce champ** (`proto/campagne_service.proto:91‑103` — `created_by` n'existe que dans `CreateCampagneRequest`, l.37) et le type `Campagne` de la gateway non plus | Le SUPERVISEUR voit sa **liste** (filtre `ListCampagnes.created_by` OK) mais **plante (`AttributeError`)** à l'ouverture ou à toute action sur une campagne. **Rôle inutilisable end‑to‑end.** Correctif trivial. |
| 🟠 Moyenne | **Mode CHÈQUE** | `ModePaiement` inclut `CHEQUE`, proposé dans **3 sélecteurs** (`facture.model.ts:4`) | Enum backend = **ESPECES / MOBILE_MONEY / VIREMENT** uniquement (`paiement/models.py:9‑11`) ; `create()` sans `full_clean()` | Un paiement « CHEQUE » est **persisté silencieusement** hors du domaine officiel, sans référence exigée. Dérive de données. |
| 🟢 Faible | **Espace abonné** | Page = placeholder, n'appelle rien ; pas d'entrée proxy | Endpoints REST complets et sécurisés | Backend prêt, front non branché (voir §5). Penser à ajouter la route au proxy le moment venu. |
| ⚪ Info | `typeEnvoi` désactivé côté front « PENDING BACKEND » | Champ demandé mais code couleur désactivé | Le type `Envoi` **expose bien** `typeEnvoi` | Fonctionnalité désactivée inutilement (hypothèse périmée). |

**Correctif SUPERVISEUR (rapide) :** ajouter `string created_by = 12;` à `CampagneResponse`, le peupler dans le serializer/`grpc_server` de campagne, et l'exposer dans le type `Campagne` de la gateway — ou faire lire `_verifier_acces_campagne` depuis la source qui porte réellement l'information.

---

## 5. Fonctionnalités manquantes (Q4)

Le SGFE couvre **l'intégralité du cycle nominal** « abonné → compteur → campagne → relevé → facture PDF → notification WhatsApp → paiement → impayés → reporting », avec une maturité supérieure à un simple MVP. Ce qui manque relève surtout du **volet financier correctif** (indispensable en production comptable) et de la **finition du portail abonné**.

### 5.1 Couverture par domaine

| Domaine | Statut | Réf. |
|---|:---:|---|
| Abonnés & compteurs (CRUD, suspension/réactivation/résiliation, remplacement compteur + historique) | ✅ Complet | `abonne/abonnes/services.py:90‑199` |
| Campagnes & saisie d'index (planif., zones, scheduler 7h, audit `ReleveAudit`, correction post‑clôture) | ✅ Complet | `campagne/campagnes/services.py:356` |
| Facturation — émission (génération auto, `prix_m3` copié, PDF WeasyPrint, historique tarifs) | ✅ Complet | `facturation/factures/services.py:107` |
| Paiements — encaissement (partiels/multiples, 3 modes, réf. obligatoire MM/Virement, atomique + `FOR UPDATE`) | ✅ Complet | `paiement/paiements/services.py:61` |
| Impayés & relances (escalade J+0/3/7/10, suspension auto, **réactivation + WhatsApp de rétablissement** au solde) | ✅ Complet | `paiement/paiements/services.py:206` |
| Notifications WhatsApp/e‑mail (6 types d'envois, traçabilité `Envoi`, renvoi manuel) | ✅ Complet | `notification/notifications/models.py:13` |
| Reporting / dashboard (read‑model CQRS, consumer idempotent) | ✅ Complet | `services/reporting/stats/*` |
| Configuration (infos société, délais, relances, suspension auto) | ✅ Complet | `config/parametres/models.py:36` |
| Utilisateurs & rôles (4 rôles, anti‑bruteforce, activation 2 canaux) | ✅ Complet | `auth/comptes/models.py:11` |
| **Avoir / annulation / rectification de facture** | ❌ Absent | corriger un relevé ne régénère pas la facture émise |
| **Annulation / remboursement de paiement** | ❌ Absent | une seule mutation `enregistrer_paiement`, irréversible |
| **Reçu de paiement (PDF)** | ❌ Absent | aucun justificatif de versement |
| **Espace abonné (frontend)** | 🟡 Back OK / front stub | `espace-abonne.component.ts:54` (« Bientôt disponible ») |
| Idempotence des paiements | 🟡 Partiel | pas de clé de déduplication (double soumission MM) |
| Estimation compteur non relevé | 🟡 Partiel | statut `ESTIME` présent, mais aucun calcul d'estimation |
| Pénalités de retard | ❌ Absent | hors périmètre SRS, mais standard en production |
| Report d'arriérés inter‑périodes | ❌ Absent | chaque facture est isolée |
| Tarification par tranches progressives + taxes/redevances | ❌ Absent | prix unique au m³ (les régies d'eau facturent le plus souvent par tranches) |
| Pagination / recherche sur les grandes listes | ❌ Absent | filtrage par statut uniquement (OK à 50 abonnés, bloquant au‑delà) |
| Audit trail métier étendu (qui a suspendu/résilié/modifié un tarif) | 🟡 Partiel | seuls les relevés sont audités |
| RGPD (export / effacement des données abonné) | ❌ Absent | — |

### 5.2 Manques priorisés

**Bloquant production**

- **Avoir / annulation / rectification de facture.** Une facture émise sur un mauvais index (ou compteur défectueux) est aujourd'hui définitive. Sans facture d'avoir ni rectificative, toute erreur métier est ingérable et non traçable comptablement.
- **Annulation / remboursement de paiement.** L'irréversibilité d'une écriture de caisse est rédhibitoire en comptabilité (erreur de montant, mauvaise facture, doublon).
- **Espace abonné (frontend).** Le lien envoyé par WhatsApp mène à un écran « Bientôt disponible » : l'abonné ne peut ni consulter son historique ni télécharger sa facture, alors que le backend est prêt et sécurisé.

**Important**

- **Idempotence des paiements** (clé de déduplication) — un double clic / double webhook Mobile Money crée deux versements.
- **Reçu de paiement (PDF)** — attendu en contexte d'encaissement espèces/mobile money et utile en cas de litige.
- **Robustesse clôture → facturation** — si Facturation est indisponible à la clôture, aucune facture n'est générée, silencieusement (voir aussi §6.1).
- **Pagination / recherche** — indispensable dès la montée en charge.
- **Audit trail métier étendu** — indispensable pour un système financier (et pour SOC 2).

**Confort / évolution**

Report d'arriérés inter‑périodes, pénalités de retard, tarification par tranches + taxes, estimation automatique des compteurs non relevés, RGPD (export/anonymisation), multi‑agence/multi‑tenant.

**À noter :** l'audit interne `docs/ETAT_DU_SYSTEME.md` (32 anomalies suivies) est précieux mais partiellement **périmé** — il déclare le service Reporting « inexistant » alors que le code l'implémente et le câble complètement.

---

## 6. Bonnes pratiques (Q5)

### 6.1 Backend — respectées, avec une dette *architecturale* (pas cosmétique)

Le backend applique réellement ses conventions : layering uniforme, type hints (0 `Any`), docstrings, `Decimal` pour l'argent, transactions, verrous de concurrence, dépendances figées, tests imposés à 80 %. La dette se concentre sur les **garanties de livraison en système distribué** :

- 🔴 **Dual‑write sans réconciliation → facture orpheline.** `initialiser_solde` est un appel gRPC *best‑effort* qui avale l'exception et renvoie `False`, **résultat ignoré** par l'appelant (`facturation/grpc_clients.py:246`, `services.py:209`). Si Paiement est indisponible pendant la génération, la facture est committée mais **aucun `SoldeFacture` n'est créé** → toute tentative de paiement lève ensuite `NOT_FOUND` → **facture impayable**, sans job de rattrapage. → Pattern **transactional outbox**, ou création paresseuse du solde au 1er paiement, ou commande de réconciliation.
- 🟠 **Crons in‑process sans verrou.** `BackgroundScheduler` démarré dans chaque process gRPC ; l'escalade impayés ne verrouille rien. Deux réplicas (canary) → **relances WhatsApp et suspensions dupliquées** (`paiement/schedulers.py:45`). → Leader‑election (verrou Redis / `pg_advisory_lock`) ou `CronJob` unique.
- 🟠 **Synchro statut facture best‑effort** → divergence durable (Paiement `PAYEE`, Facturation reste `IMPAYEE`, dashboard faux) (`paiement/grpc_server.py:73`).
- 🟠 **`EnregistrerPaiement` non idempotent** → double versement possible en cas de retry après commit (`paiement/grpc_server.py:55`).
- 🟡 **Incohérence de type** sur l'index compteur (`Float` côté campagne vs `Decimal` ailleurs) ; artefacts flottants possibles sur la consommation affichée (le **montant** reste exact car recalculé en `Decimal`).
- 🟡 Duplication des intercepteurs/clients gRPC (8 copies avec dérive), pas de `mypy`/`pyright` en CI (la règle « jamais de `Any` » tient par discipline), dérive doc (ReportLab annoncé vs **WeasyPrint** réel).

*Gestion de l'argent : conforme et robuste — aucun calcul monétaire en `float`. Seule la consommation (m³, pas l'argent) est en `Float` côté campagne.*
*Couverture de tests backend : ~80‑88 %, réellement imposée par la CI (56 fichiers de test, ~786 fonctions `test_`).*

### 6.2 Frontend — moderne et propre, mais sous‑testé

Points forts déjà cités (§2.1). Faiblesses de pratique :

- 🟠 **Couverture ~10 %** (13 `*.spec.ts` pour ~130 unités) ; la logique la plus critique — **file offline terrain**, refresh de session, gardes de rôle — n'a **aucun** test. **E2E non fonctionnel** (les Page Objects visent un `data-testid="title"` qui n'existe nulle part).
- 🟠 **`strict` TypeScript désactivé** (`tsconfig.json` sans `"strict": true` ni `strictTemplates`) malgré la règle « typage strict » — la qualité actuelle tient sans le filet du compilateur.
- 🟡 **Pas de codegen GraphQL** (types manuels → dérive silencieuse possible vs schéma), temps réel seulement partiel (`progressionUpdated` non branché), composants volumineux, accessibilité des formulaires perfectible (`aria-label` plutôt que `<label for>`).

### 6.3 DevOps / CI‑CD / Infra — excellent « build », « run » manquant

**Forts :** Dockerfiles backend exemplaires (multi‑stage, digest SHA, non‑root, healthcheck), CI backend de niveau production (lint + Bandit + `pip-audit` + gitleaks + gate 80 % + Trivy + SBOM + provenance + **cosign**, actions épinglées par SHA), Dependabot multi‑écosystème, **canary frontend réel** (5 %→25 %→100 % par pondération nginx + gates manuels + workflow de rollback), whatsapp‑service robuste (session Redis `RemoteAuth`, reconnexion à backoff, arrêt gracieux, comparaison temps constant).

**Faibles (risques prod) :**

- 🔴 **Aucun déploiement backend.** Les 8 services + gateway sont buildés/publiés sur GHCR mais **jamais déployés** : pas de manifeste k8s, pas de compose de prod backend, pas de workflow CD. La mise en prod du cœur métier est indéfinie.
- 🔴 **Aucune sauvegarde** (ni `pg_dump`/WAL, ni backup Redis) → perte de données définitive sur incident disque.
- 🟠 **Observabilité absente** alors que promise (0 usage OTel/Prometheus en code, pas de `/metrics`, pas de logs `trace_id`) → diagnostic d'incident impossible.
- 🟠 **Rupture d'intégration prod frontend probable** : le nginx frontend proxie `/graphql → http://api-gateway:8080`, mais `frontend/docker-compose.prod.yml` **ne définit aucun service `api-gateway`** → à router explicitement vers le backend réel.
- 🟠 **Tests frontend fictifs en CI** (`playwright test --pass-with-no-tests`, dossier `e2e/specs` inexistant) → 0 test exécuté ; pas de Trivy sur l'image frontend.
- 🟠 **SPOF multiples** : Redis unique (session WhatsApp + pub/sub), `whatsapp-web.js` mono‑session (lib non officielle → risque de bannissement du compte), PostgreSQL mono‑instance ×8 sans réplication.
- 🟡 `DEBUG=True` + secrets Django/JWT/DB codés en dur dans le compose, ports DB/gRPC publiés, aucune limite de ressources ni `restart policy` (sauf whatsapp), Dockerfile frontend en retrait (non‑root/digest absents), `/health` whatsapp toujours 200 même déconnecté, tests sur SQLite alors que la prod est PostgreSQL.

### 6.4 Écart documentation vs réalité (transversal)

La documentation (`CLAUDE.md`, `docs/`) décrit une **cible idéalisée** ; le code réel diverge — le plus souvent **en faveur du code** côté frontend, et **en défaveur** côté infrastructure :

| Élément | Doc annonce | Réalité du code |
|---|---|---|
| Orchestration | Kubernetes + Minikube (dossier `k8s/`) | **Inexistant** — orchestration réelle = `docker compose` |
| Observabilité | Prometheus/Loki/Jaeger/Grafana + OTel + `/metrics` | **Non implémentée** (dépendances déclarées, 0 usage) |
| Déploiement | Canary (tout le système), MacBook + ngrok | Canary **frontend seulement** (SSH vers `aquabill.cm`) ; **backend : aucun** |
| UI frontend | Angular Material 3 | **PrimeNG** |
| Formulaires / data | Signal Forms, `httpResource`, Selectorless | `FormsModule`+`ngModel` sur signals ; Apollo/HttpClient ; sélecteurs classiques |
| Rôles (frontend) | 3 rôles (`ADMIN/AGENT/COMPTABLE`) | **4 rôles** (SUPERVISEUR géré) — le réel est plus complet |
| Zoneless | `provideExperimentalZonelessChangeDetection` | API **stable** `provideZonelessChangeDetection()` — meilleur |
| Génération PDF | ReportLab | **WeasyPrint** |
| Couverture tests | > 80 % (front & back) | Back ~80‑88 % (réel) ; **front ~10 %** |

*Recommandation transverse : réaligner la documentation sur le code, ou livrer les éléments manquants — un écart de cette ampleur induit en erreur les nouveaux arrivants et un futur auditeur.*

---

## 7. Recommandations priorisées (Q6)

Effort indicatif : **S** ≈ < 1 j, **M** ≈ quelques jours, **L** ≈ 1‑3 semaines.

### 7.1 P0 — Bloquant (avant toute mise en production)

1. **Révoquer et rotationner la clé API Brevo** + le mot de passe admin, et externaliser tous les secrets (coffre / secrets manager / K8s Secrets chiffrés). Vérifier que les `.env` n'ont **jamais** été committés (`git log --all -- **/.env`). **(S)**
2. **Sécuriser la couche gRPC** : mTLS entre services **ou** isolation réseau stricte (ne plus publier 50051‑50058, réseaux Docker cloisonnés) + jeton d'identité signé par la gateway ; idéalement revalider l'autorisation côté service. **(L)**
3. **Supprimer les défauts insecure** : échec au démarrage si `DJANGO_SECRET_KEY`/`JWT_SECRET_KEY` absents, `DEBUG=False` par défaut, et passer à un **JWT asymétrique (RS256)** signé par le seul auth‑service. **(M)**
4. **Corriger le rôle SUPERVISEUR** (`created_by` dans `CampagneResponse` + type gateway) et **le mode CHÈQUE** (l'ajouter à l'enum backend ou le retirer du front + `full_clean()`). **(S)**
5. **Livrer une cible de déploiement backend** (compose de prod ou manifestes) **avec migrations pilotées et sauvegardes PostgreSQL** (+ test de restauration). **(L)**
6. **Rate limiting** (par IP + par compte) sur login/OTP/reset et **durcir GraphQL** (désactiver introspection/GraphiQL hors dev, limites de profondeur/complexité). **(M)**
7. **Mettre en place une piste d'audit** métier immuable (qui/quoi/quand : création/désactivation d'utilisateur, paiement, changement de tarif, suspension) + logs structurés — **à démarrer tôt** pour accumuler les preuves SOC 2. **(M)**
8. **Implémenter l'espace abonné côté frontend** (requête par token, affichage facture/solde, états token invalide/expiré) + route proxy. **(M)**

### 7.2 P1 — Important (0‑3 mois)

9. **Fiabiliser les flux distribués** : outbox transactionnel (ou création paresseuse du solde) pour supprimer les **factures orphelines** ; rendre la synchro de statut et la clôture→facturation rejouables ; **clé d'idempotence** sur les paiements. **(L)**
10. **Volet financier correctif** : facture d'**avoir**/rectification, **annulation/remboursement** de paiement, **reçu PDF**. **(L)**
11. **Observabilité réelle** : OTel SDK + exporteur OTLP, `/metrics`, healthchecks applicatifs, logs `trace_id` — ou aligner la doc si non retenu. **(M‑L)**
12. **Tests frontend** : cibler d'abord la file offline terrain, le refresh+retry, les gardes de rôle ; écrire de vrais e2e (ajouter les `data-testid`) et lancer Vitest en CI. **(M)**
13. **Verrouiller les crons** (leader‑election) et **activer `strict` TypeScript** + `strictTemplates`. **(M)**

### 7.3 P2 — Moyen terme / évolution

14. Pagination + recherche sur les listes ; **codegen GraphQL** ; `mypy --strict` en CI ; uniformiser l'index compteur en `Decimal`.
15. En‑têtes de sécurité (HSTS/CSP), Dockerfile frontend durci (digest + non‑root + Trivy), limites de ressources + `restart policy`, `/health` whatsapp qui renvoie 503 si déconnecté.
16. Chiffrement au repos des PII abonné ; RGPD (export/anonymisation).
17. Évolutions métier : tarification par **tranches progressives** + taxes/redevances, report d'arriérés, pénalités de retard, estimation des compteurs non relevés, redondance (Redis Sentinel, réplication PostgreSQL), file d'attente + rate‑limit WhatsApp.

### Feuille de route synthétique

| Horizon | Objectif | Actions | Effort |
|---|---|---|---|
| **Immédiat** | Stopper l'exposition | Rotationner Brevo/admin, externaliser secrets, corriger SUPERVISEUR + CHÈQUE | S‑M |
| **1 mois** | Sécuriser & rendre déployable | mTLS/isolation gRPC, défauts sûrs + RS256, rate limiting + durcir GraphQL, cible de déploiement backend + backups, audit trail | L |
| **1‑3 mois** | Fiabiliser & compléter | Outbox/idempotence, avoir/remboursement/reçu, espace abonné front, observabilité, tests front, crons verrouillés | L |
| **3‑6 mois** | Industrialiser | Pagination/recherche, codegen, RGPD/chiffrement PII, tranches tarifaires, redondance/HA, réaligner la doc | M‑L |

---

## 8. Checklist exhaustive — « Ready to Prod » & Alignement complet

Cette checklist décline la feuille de route (§7) en **tâches unitaires cochables**, ordonnées par priorité puis par thème. Elle vise deux objectifs de *definition of done* :

- 🏭 **Ready to Prod** = tous les items **P0** + **P1** cochés (les portes Go/No‑Go sont en fin de section).
- 🔗 **Complètement aligné** = tous les items marqués 🔗 cochés (front ⇄ back sans écart de contrat ni dérive).

**Effort indicatif :** **S** ≈ < 1 j · **M** ≈ quelques jours · **L** ≈ 1‑3 semaines. Les références `fichier:ligne` pointent le point d'entrée du correctif.

### 🔴 P0 — Bloquant (avant toute mise en production)

**A. Secrets & configuration**

- [ ] Révoquer et régénérer la **clé API Brevo** (`services/auth/.env:31`) et le mot de passe admin `Admin1234!` (`.env:43`). *(S)* — **❓ Incertain.** Aucune PR ne mentionne explicitement une rotation de la clé Brevo ; le fail-fast sur les secrets (item suivant) empêche une valeur par défaut vide mais ne prouve pas la rotation d'une clé déjà exposée. À confirmer hors code (rotation manuelle côté fournisseur).
- [x] Vérifier que les `.env` n'ont **jamais** été committés, purge si besoin. *(S)* — **✅ Fait, vérifié le 03/09.** `git log --all --diff-filter=A -- "**/.env" ".env"` renvoie 0 résultat dans les deux dépôts — aucun `.env` n'a jamais été ajouté à l'historique. Aucune purge nécessaire. PR #110 (22/07) ajoute en plus un hook pre-commit anti-commit de `.env` pour l'avenir.
- [x] Externaliser **tous** les secrets vers un coffre et retirer les valeurs en dur de `docker-compose.yml`. *(M)* — **✅ Fait côté code, PR #169 (fusionnée 03/09).** `ansible/roles/secrets` complété avec l'inventaire réel des secrets (bug de chemin des clés RS256 corrigé au passage) ; **`docker-compose.yml` interpole désormais `${DJANGO_SECRET_KEY:-...}`/`${POSTGRES_PASSWORD:-...}`** au lieu de les coder en dur (valeurs de repli identiques en local, vérifié avec/sans surcharge via `docker compose config`) — le provisioning a maintenant un effet réel sur tous les services, plus seulement `db-backup`. Aucun identifiant AWS disponible dans cet environnement pour un provisioning réel ; IaC validée syntaxiquement seulement.
- [x] Supprimer toutes les **valeurs par défaut** de secrets dans les `settings.py` ; **échec au démarrage** si absents. *(S)* — **✅ Fait — PR #99 (2026-07-20).** `gateway/gateway/settings.py:12` et `services/auth/auth/settings.py:15` lèvent une exception si `DJANGO_SECRET_KEY`/`INTERNAL_GRPC_KEY` sont absents (confirmé en direct dans cette session : `CleInterneManquante`).
- [x] `DEBUG=False` par défaut partout. *(S)* — **✅ Fait — PR #99.** Vérifié sur gateway + 3 services (`env.bool("DJANGO_DEBUG", default=False)`).
- [x] Migrer le JWT en **RS256 asymétrique**. *(M)* — **✅ Fait — PR #100 (2026-07-20).** `services/auth/auth/settings.py:166` `"ALGORITHM": "RS256"`, paire de clés asymétrique.
- [x] Nettoyer la config morte/trompeuse (`BCRYPT_ROUNDS`, `JWT_SECRET_KEY="changeme"`). *(S)* — **✅ Fait — PR #111 (2026-07-22).**

**B. Sécurité inter‑services & réseau**

- [x] Activer **mTLS** entre tous les services gRPC ou déployer un service mesh. *(L)* — **✅ Fait — PR #168 (ouverte, non fusionnée, 03/09/2026).** CA interne + certificat généré (`scripts/generate-grpc-certs.sh`), `add_secure_port`/`secure_channel` sur les 9 composants avec repli en clair explicite pour les tests en mémoire. 1131/1131 tests verts, aucune régression. Vérification live (`docker compose up` réel) encore en attente, une stack tournait déjà localement au moment du développement.
- [x] Propager un **jeton d'identité signé par la gateway** + intercepteur d'authz côté chaque service. *(L)* — **✅ Fait — PR #138 (2026-08-31).** Chaque service a désormais son `grpc_auth.py`/`grpc_interceptors.py` avec tests dédiés. **Mais** dupliqué **8 fois** plutôt que via une lib partagée — voir §L `sgfe_common.grpc`, dont le besoin est donc *plus* justifié qu'avant.
- [x] **Isolation réseau** : ne plus publier les ports gRPC/PostgreSQL sur l'hôte. *(M)* — **✅ Fait — PR #104 (2026-07-21).** Confirmé : plus aucun mapping `500XX:500XX`/`543X:543X` vers l'hôte dans `docker-compose.yml`.
- [x] whatsapp‑service **fail‑closed** si clé absente, `/health` 503 si déconnecté. *(S)* — **✅ Fait — PR #101 (2026-07-20).** `whatsapp-service/server.js:25` refuse de démarrer sans `WHATSAPP_INTERNAL_API_KEY` ; `/health` renvoie 503 si déconnecté (couvre aussi l'item I correspondant).

**C. Durcissement des accès (edge)**

- [x] **Rate limiting** sur login / refresh / OTP / reset. *(M)* — **✅ Fait — PR #106 (2026-07-21).** `nginx/default.conf:10` `limit_req_zone ... rate=30r/s` + `limit_req_status 429`. Par IP au niveau nginx ; pas de throttle DRF par compte constaté séparément, mais couvre l'essentiel.
- [x] Désactiver **introspection + GraphiQL** hors dev. *(S)* — **✅ Fait — PR #103 (2026-07-20).** `gateway/gateway/urls.py:18` `graphql_ide="graphiql" if settings.DEBUG else None`.
- [x] Ajouter **limites de profondeur / complexité / coût** GraphQL. *(M)* — **✅ Fait — PR #103.** `gateway/schema/schema.py:22-24` : `QueryDepthLimiter(max_depth=12)`, `MaxAliasesLimiter(max_alias_count=50)`, `MaxTokensLimiter(max_token_count=5000)`.
- [x] Réduire l'**access token** à 15‑30 min. *(S)* — **✅ Fait — PR #109 (2026-07-22).** ⚠️ Le résumé stack de `CLAUDE.md` racine mentionne encore « 24h » — documentation à corriger (doublon avec l'item Q).
- [x] Forcer le cookie refresh `Secure=True` en prod indépendamment de `DEBUG`. *(S)* — **✅ Fait.** `gateway/gateway/settings.py:110` `default=not DEBUG`.
- [x] En‑têtes de sécurité : `SecurityMiddleware` + HSTS + CSP + `X-Content-Type-Options` + `Referrer-Policy`. *(M)* — **✅ Fait — PR #102 (2026-07-20).**
- [x] Confirmer l'intention de `infosSociete` non authentifié, sinon `require_auth`. *(S)* — **✅ Fait — PR #169 (fusionnée 03/09).** Le motif « alimente les PDF » était faux (le PDF passe par un appel gRPC direct côté Facturation) ; seul consommateur frontend confirmé = écran Configuration, déjà `roleGuard(['ADMIN'])`. `require_auth(info)` ajouté, 255/255 tests gateway.

**D. Bugs d'alignement bloquants**

- [x] 🔗 **Corriger le rôle SUPERVISEUR** (`created_by` dans `CampagneResponse`). *(S)* — **✅ Fait — PR #95/#96 (2026-07-19), complété PR #126 (2026-07-29).** Le bug était **déjà en cours de correction pendant la rédaction de cet audit** (17 juillet).
- [x] 🔗 **Corriger le mode CHÈQUE**. *(S)* — **✅ Fait, par le retrait frontend.** `grep CHEQUE src/app/` (frontend) = 0 résultat (contre `facture.model.ts:4` + 3 sélecteurs à l'origine). Le backend n'a pas ajouté l'enum (`ModePaiement` toujours `ESPECES/MOBILE_MONEY/VIREMENT/AVOIR`) — cohérent, l'audit proposait l'un **ou** l'autre.

**E. Déploiement & protection des données**

- [x] Créer une **cible de déploiement backend** réelle. *(L)* — **✅ Fait, et dépassé.** PR #108 (22/07, compose.prod) puis PR #144 (31/08, Ansible/AWS) ; `docs/INFRASTRUCTURE_AWS.md` + `docs/CHAINE_DE_LIVRAISON.md` (28/08) détaillent une cible EC2 t4g.medium complète et chiffrée. Voir encart §10 : la cible a changé d'Azure vers **AWS**.
- [x] **Pipeline CD backend** avec migrations pilotées. *(M)* — **✅ Fait — PR #142/#143 (2026-08-31).** Images signées tirées en prod (pas de rebuild), migrations explicitement gatées avant rollout.
- [x] 🔗 Router `/graphql` du frontend de prod vers la **vraie gateway**. *(S)* — **✅ Fait — PR #145 (2026-09-01, backend) + confirmé côté frontend.** Résolu via un réseau Docker externe partagé (`sgfe-edge`) entre les deux dépôts Compose plutôt qu'un service dupliqué — meilleure solution que celle suggérée à l'origine.
- [x] **Sauvegardes PostgreSQL** automatiques (dump + archivage) pour les 8 bases. *(M)* — **✅ Fait — PR #107 (2026-07-21).** `scripts/backup-databases.sh` : dump gzip horodaté des 8 bases + rétention configurable, service `db-backup` en prod.
- [x] **Tester la restauration** (restore drill documenté). *(S)* — **✅ Fait et exécuté réellement — PR #169 (fusionnée 03/09).** `scripts/test-restore.sh` : restauration sur un Postgres jetable, 4 tables vérifiées (sortie réelle jointe à la PR), conteneur temporaire détruit ensuite.
- [x] Chiffrer les sauvegardes. *(S)* — **✅ Fait — PR #169.** AES-256-CBC/PBKDF2 via `BACKUP_ENCRYPTION_KEY` (fail-fast si absente), roundtrip chiffrement/déchiffrement testé.
- [x] Ajouter `restart: unless-stopped` + limites de ressources sur tous les conteneurs. *(S)* — **✅ Fait — PR #169 (fusionnée 03/09).** Les 21 services ont `restart: unless-stopped` ; limites par rôle (Django 512M/1cpu, gateway 768M/1.5cpu, whatsapp-service 1.5G/1.5cpu). Vérifié empiriquement via `docker inspect` que `deploy.resources.limits` prend bien effet sous `docker compose up` classique (pas seulement swarm).

### 🟠 P1 — Critique (fiabilité, complétude essentielle, conformité)

**F. Robustesse distribuée**

- [ ] **Transactional outbox**. *(L)* — **Non fait**, mais l'alternative recommandée par l'audit lui-même a été choisie et livrée (item suivant).
- [x] Alternative : **création paresseuse** du `SoldeFacture` + **commande de réconciliation**. *(M)* — **✅ Fait — PR #115/#116 (2026-07-22).** "solde idempotent + commande reconcilier_soldes (factures orphelines)".
- [x] **Clé d'idempotence** sur `EnregistrerPaiement`. *(M)* — **✅ Fait — PR #114 (2026-07-22).**
- [x] Rendre la **synchro de statut facture** rejouable + **recalcul périodique** reporting. *(M)* — **✅ Fait — PR #170 (fusionnée 03/09).** Job `CronTrigger` nocturne (verrou `pg_advisory_lock`, clé dédiée `4210004`) qui recalcule `StatsFacturation`/`StatsPaiements` depuis Facturation/Paiement en plus du consumer Redis Streams déjà idempotent. `services/reporting` : 34→46 tests.
- [x] **Robustesse clôture → facturation** : retry/file + régénération manuelle. *(M)* — **✅ Fait — PR #170.** `Campagne.facturation_en_attente` posé si l'appel échoue à la clôture ; job horaire (`pg_advisory_lock`, clé `4210003`) rejoue le même appel `notifier_campagne_cloturee` sans dupliquer la logique de génération. Bug corrigé au passage : la valeur de retour de cet appel n'était jusque-là jamais lue. `services/campagne` : 117→136 tests.
- [x] **Verrouiller les crons** (`pg_advisory_lock`). *(M)* — **✅ Fait — PR #118 (2026-07-23).** Le pattern a été répliqué avec succès pour la nouvelle diffusion WhatsApp (`services/notification/notifications/schedulers.py`, PR #167).
- [x] Logger les **exceptions avalées**. *(S)* — **✅ Fait — PR #113 (2026-07-22).**
- [x] **Redis Streams — redélivraison bornée + dead-letter.** *(M)* — **✅ Fait — PR #177 (fusionnée 03/09), incident réel corrigé.** Découvert en rédigeant le runbook (PR #176) : un événement invalide (`campagne_id=""`) faisait échouer la transaction Django *avant* le `XACK`, donc l'événement restait indéfiniment "pending" et était rejoué à chaque redémarrage du consumer — **191 entrées bloquées observées sur la pile partagée**, `times_delivered` jusqu'à 16. Correctif : `MAX_DELIVERY_ATTEMPTS=5` (compté via `XPENDING`, pas un compteur en mémoire — survit aux redémarrages), au-delà l'événement est déplacé vers un flux `reporting:stream:dead-letter` puis acquitté. `services/reporting` : 46→50 tests. **Nettoyage opérationnel fait le 04/09** : la pile partagée reconstruite avec le code corrigé (2 redémarrages de `reporting-service`, le rattrapage au démarrage ne rejoue que le PEL courant) — les 191 entrées confirmées `ValidationError: '"" n'est pas un UUID valide'` sont passées de `pending` à `reporting:stream:dead-letter`, `XPENDING` à 0, service resté sain.

**G. Volet financier correctif (complétude essentielle)**

- [x] Facture d'**avoir / annulation / rectification** + régénération après correction de relevé. *(L)* — **✅ Fait.** 5 PR pour le volet avoir/annulation (#123, #124 le 23/07 ; #162, #163, #165 le 02/09) : trop-perçu → avoir reportable, avoir manuel, resynchronisation des annulations, décrément des stats Reporting. La régénération après correction de relevé, qui manquait spécifiquement, est désormais un flux nommé distinct — **PR #170 (fusionnée 03/09)** : `regenerer_facture_si_necessaire`, appelé depuis `CorrigerReleve` si une facture existe déjà pour la période, avec retry si Facturation est indisponible au moment de la correction.
- [x] **Annulation / remboursement** de paiement. *(M)* — **✅ Fait — PR #117 (2026-07-23)**, correctif de robustesse PR #140 (31/08).
- [x] **Reçu de paiement (PDF)**. *(M)* — **✅ Fait — PR #119 (2026-07-23)**, envoi WhatsApp auto (PR #121), envoi depuis le versement (PR #159, 02/09) ; un bug de solde sur ce reçu a été corrigé aujourd'hui même (PR #166, 03/09, cette session).
- [x] Gestion du **trop‑perçu / crédit** abonné. *(M)* — **✅ Fait — PR #123 (23/07)**, règle de priorité ajoutée après coup (PR #141, 31/08 : le trop-perçu éteint les impayés avant de devenir un avoir).

**H. Espace abonné (complétude + alignement)**

- [x] 🔗 Implémenter le composant **espace‑abonné**. *(M)* — **✅ Fait des deux côtés.** Backend : `gateway/schema/espace_abonne.py` (258 lignes), itéré 4 fois (PR #151, #164, #166). Frontend : `espace-abonne.component.ts` (334 lignes), états loading/ready/invalid/error, distingue dette échue/non échue — dépasse le périmètre minimal demandé, décrit comme « coquille vide » en juillet.
- [x] 🔗 Ajouter la route `espace‑abonne` à `proxy.conf.json` + nginx prod. *(S)* — **✅ Fait.**
- [ ] (Option) **Paiement en ligne** dans l'espace abonné. *(L)* — **Non fait, mais volontaire** : conforme à la décision §10.2 de cet audit lui-même (« consultation seule, paiement en ligne reporté »). Pas un écart.

**I. Observabilité (exploitation + SOC 2 CC7)**

- [ ] Instrumenter **OpenTelemetry** réellement. *(M)* — **Toujours non fait** (revérifié 03/09 : 0 `TracerProvider` en dehors des dépendances déclarées).
- [ ] Exposer **`/metrics`** Prometheus. *(M)* — **Toujours non fait** (0 occurrence `prometheus_client`/`django_prometheus`/route `/metrics`).
- [ ] **Logs JSON structurés** avec `trace_id`. *(M)* — **Toujours non fait** (0 `jsonlogger`/`structlog` dans les settings).
- [ ] Déployer la stack **Prometheus/Grafana/Loki/Jaeger**. *(M)* — **Non fait**, dépend entièrement des 3 items ci-dessus.
- [x] **Healthchecks applicatifs** sur la gateway + `depends_on: service_healthy`. *(S)* — **✅ Fait — PR #175 (fusionnée 03/09).** Sonde TCP sur les 8 microservices Django (en plus de la sonde GraphQL gateway déjà posée par PR #137) + `depends_on: condition: service_healthy` sur toute la chaîne de dépendances. Vérifié en direct le 03/09 (soir) : stack de 21 conteneurs reconstruite, tous `healthy` (hors `whatsapp-service`, `unhealthy` car aucune session WhatsApp Web authentifiée dans cet environnement — comportement attendu, pas un défaut du healthcheck).
- [x] `/health` whatsapp renvoie **503** si déconnecté. *(S)* — **✅ Fait — PR #101** (même PR que le fail-closed §B).
- [ ] **Règles d'alerte** + routage on‑call. *(M)* — **Non fait**, dépend des items d'observabilité ci-dessus.

**J. Piste d'audit & conformité SOC 2**

- [ ] **Journal d'audit métier immuable**. *(M)* — **Toujours non fait — à ne pas confondre avec une PR au titre trompeur.** PR #112 (22/07) ne livre qu'un **document de conception** (« docs(audit): conception identite -> journal d'audit immuable », intégré ci-dessous en §10.7). Recherche de code (`AuditLog`/`AuditEvent`/`JournalAudit`) : 0 résultat. C'est le type d'erreur qu'une vérification au seul titre de PR aurait manqué.
- [ ] **Journalisation de sécurité** centralisée et inviolable. *(M)* — **Non fait**, dépend de l'item ci-dessus.
- [ ] Politique de **rétention des logs** + horodatage fiable. *(S)* — **Non fait** (revérifié 03/09 : aucun bloc `LOGGING` personnalisé dans les `settings.py`).
- [ ] **Démarrer la collecte de preuves tôt**. *(continu)* — Sans objet tant que J1/J2 ne sont pas livrés.

**K. Tests (fiabilité)**

- [x] Tests frontend prioritaires : file offline terrain, refresh+retry, gardes de rôle, interceptor JWT. *(M)* — **✅ Fait — PR #142 (fusionnée 31/08).** 40 nouveaux tests (241→281, 29→34 fichiers) couvrant les 4 parcours. Deux constats de conception documentés par des tests plutôt que corrigés (hors périmètre) : la file hors-ligne synchronise en LIFO pas FIFO ; le refresh REST (contrairement au refresh GraphQL) ne mutualise pas les requêtes concurrentes. Aucun vrai bug trouvé.
- [ ] 🔗 Vrais **tests e2e Playwright**. *(M)* — **🟡 Partiel, étendu mais toujours incomplet.** PR #143 (fusionnée 31/08) ajoute un spec saisie-index (gaté `E2E_LIVE_BACKEND=1`, skip propre en CI). Le spec paiement est écrit mais en `test.skip()` **permanent** : vérifié dans le code backend que l'enregistrement d'un paiement déclenche un envoi WhatsApp réel et inconditionnel (`_propager_versement`), sans possibilité de l'éviter côté UI — jamais exécuté contre un backend réel, à raison.
- [x] Lancer **Vitest** en CI. *(S)* — **✅ Fait.** `.github/workflows/ci.yml` : `npx ng test --no-watch`.
- [x] Job CI backend sur **PostgreSQL**. *(S)* — **✅ Fait — PR #169 (fusionnée 03/09).** `postgres:16-alpine` ajouté aux 8 jobs `test-*` concernés, SQLite gardé en défaut local. Vérifié réellement (pas que le YAML) : suite `config` (31/31) exécutée contre un Postgres 16 jetable.
- [x] **Trivy** sur l'image frontend. *(S)* — **✅ Fait — PR #143 (fusionnée 31/08).** Étape ajoutée sur le modèle du backend, exécutée réellement en local (Trivy 0.72.0). **Découverte** : 2 CRITICAL + 35 HIGH dans les paquets Alpine de `nginx:1.27-alpine` (image de base non reconstruite depuis avril 2025, pas le code applicatif) — fera échouer la CI dès son premier run tant que le tag n'est pas bumpé.
- [ ] **Test de charge / performance**. *(M)* — **🟡 Partiel, point de départ seulement.** PR #143 ajoute `loadtest/basic.js` (k6, 2-3 requêtes GraphQL de lecture) — explicitement documenté comme non représentatif d'un vrai test de charge de production, faute d'environnement de staging à ce jour. Cible mise à jour le 03/09 (PR #146) : `https://localhost:8443` + `--insecure-skip-tls-verify` après le durcissement TLS de nginx.
- [ ] **Test de pénétration** avant go‑live. *(M)* — **Non fait.**

### 🟡 P2 — Important (industrialisation, qualité, montée en charge)

**L. Qualité du code & alignement fin**

- [x] Activer **`strict` TypeScript** + `strictTemplates`. *(S)* — **✅ Fait, et au-delà.** `tsconfig.json` : `strict:true` + `noImplicitOverride`, `noPropertyAccessFromIndexSignature`, `noImplicitReturns` ; `strictTemplates`/`strictInjectionParameters`/`strictInputAccessModifiers` côté Angular. PR de la refonte du 27/08.
- [x] 🔗 Introduire **graphql‑codegen**. *(M)* — **✅ Fait.** `generated.ts`, `verify:codegen`, discipline de partage de fragments documentée dans `CLAUDE.md` avec des exemples de bugs passés que le codegen empêche désormais.
- [x] **`mypy --strict`** en CI backend. *(S)* — **✅ Fait — PR #181 + #183 + #184 (fusionnées 03-04/09).** `mypy.ini` par service + `requirements-dev.txt` ; **les 9 composants à 0 erreur `mypy --strict`, tests inchangés** : `auth` (120), `abonne` (107), `campagne` (136), `config` (45), `facturation` (195), `notification` (147), `paiement` (199), `reporting` (50), `gateway` (277). **Incident de parcours révélateur** : le câblage CI initial (#181/#183) n'avait en réalité **jamais tourné pour 6 des 9 jobs** — le path-filter GitHub Actions les marquait `skipped` à chaque push sans que le `ci-status` global ne le signale, masquant un vrai bug sur `auth` (l'import Django par mypy, hors du mode `TESTING` de `manage.py test`, tentait de lire des clés JWT RS256 absentes du checkout CI). Trouvé et corrigé le 04/09 en forçant délibérément un run complet (nouvelle branche, diff large) ; les 9 jobs confirmés verts sur un run réel, pas seulement localement.
- [x] 🔗 Uniformiser l'**index compteur** en `DecimalField`. *(S)* — **✅ Fait — PR #171 (fusionnée 03/09).** `campagne/models.py` : `FloatField` → `DecimalField(10,3)`, migration écrite, conversions gRPC alignées sur le motif déjà utilisé pour l'argent.
- [x] Extraire une **lib partagée** `sgfe_common.grpc`. *(M)* — **✅ Fait — PR #174 (fusionnée 03/09).** Source canonique unique (`libs/sgfe_common/`) + script de synchronisation avec vérification de dérive par hash (pas un package Python installé — choix documenté : contexte de build Docker isolé par service, et un test dépendant du nom du logger). Zéro changement de comportement, 1131/1131 tests identiques avant/après. **Dérive réelle trouvée et corrigée — PR #185 (fusionnée 04/09)** : les campagnes mypy (#181/#183) avaient corrigé le typage sur chaque copie de service sans jamais reporter le correctif vers la source canonique, et avaient même convergé vers deux styles différents (6 services vs 3) — `sync-grpc-lib.sh --check` le détectait mais ne tournait nulle part en CI. Réconcilié + job CI dédié ajouté, qui tourne toujours (pas de path-filter) et bloque `ci-status` en cas de récidive.
- [x] Découper les composants volumineux. *(M)* — **✅ Fait — PR #144 (fusionnée 03/09, frontend).** `campagne-detail`/`abonne-detail`/`facture-detail` éclatés en sous-composants cohésifs, zéro changement de comportement visible, poids CSS en baisse.
- [x] Accessibilité formulaires : `<label for>` explicites. *(S)* — **✅ Fait — PR #144.** Revue de tous les formulaires de saisie du dépôt : la quasi-totalité utilisait déjà `label[for]` ou le motif de label englobant (`app-auth-field`) — un seul vrai manque trouvé et corrigé (select Statut de `abonne-form`).
- [x] 🔗 Brancher `progressionUpdated`. *(S)* — **✅ Fait.** `campagne-detail.component.ts:475,484`, motif repris à l'identique pour `diffusionProgressionUpdated` (nouvelle fonctionnalité de diffusion, 03/09).
- [x] Ajouter `isSuperviseur` à `AuthService`. *(S)* — **✅ Fait.** `auth.service.ts:88`.
- [x] 🔗 Retirer le code vestigial. *(S)* — **✅ Fait — dernier point corrigé le 04/09 (PR #152, frontend).** `typeEnvoi` réactivé côté frontend ; le resolver `dashboard` est désormais utilisé (Reporting Service livré) — n'est plus mort ; `extensions.grpc_code` (fallback jamais atteint, 0 occurrence côté gateway) retiré de `extractGqlError()`. 307/307 tests frontend inchangés.
- [x] Nettoyer les artefacts `.coverage` du dépôt. *(S)* — **✅ Fait, 03/09 (soir).** Les 4 fichiers `.coverage` (gateway, reporting, auth, abonné) n'étaient pas trackés (déjà dans `.gitignore`) — supprimés localement, rien à committer.

**M. Performance & montée en charge**

- [x] **Pagination + recherche** sur les listes. *(M)* — **✅ Fait côté serveur (PR #172, fusionnée 03/09) ET côté UI (PR #145, fusionnée 03/09).** Serveur : `limit`/`offset` réels + `total` (via `abonnesCount`/`facturesCount`/`paiementsCount`) sur les 3 listes, rétrocompatibilité stricte vérifiée par test. UI : `abonnes-list`/`factures-list` consomment réellement la pagination serveur (repli propre sur chargement complet quand un filtre non supporté côté gateway est actif — recherche texte, quartier) ; `app-data-table` étendu d'un mode `serverSide` rétrocompatible (défaut inchangé sur les 9 usages existants). `paiements-list` **non migrée, volontairement** : aucun filtre exposé par cet écran ne correspond aux arguments serveur disponibles, et les KPI + l'export CSV exigent le jeu complet en mémoire. 289→307 tests frontend. Vérifié en direct dans le navigateur contre la stack backend reconstruite (03/09, soir).
- [x] Index DB additionnels selon les requêtes réelles. *(S)* — **✅ Fait — PR #171.** Index ciblés sur les requêtes chaudes identifiées dans facturation/paiement, chacun documenté avec la requête qu'il sert.
- [x] Cache (config, tarifs) si nécessaire. *(S)* — **✅ Fait — PR #171.** Cache Redis court sur `GetConfig`/`GetInfosSociete` avec invalidation explicite à la mise à jour.

**N. Infra & résilience (SPOF)**

- [x] Redondance **PostgreSQL** (réplication + failover). *(L)* — **✅ Fait en PoC — PR #173 (fusionnée 03/09).** Streaming replication primaire+réplique testée réellement (écriture primaire → visible réplique ; écriture réplique → rejetée). Pas de failover automatique (Patroni/repmgr manquants) — point de départ honnête, pas une HA complète.
- [x] **Redis** en réplication / Sentinel. *(M)* — **✅ Fait en PoC — PR #173.** Sentinel 3 nœuds réel, panne du maître réellement provoquée, failover observé de bout en bout. **Limite** : aucun service Django n'utilise un client Sentinel-aware — l'appli ne suivrait pas le basculement sans changement de code côté client.
- [x] **WhatsApp** : file d'attente + rate‑limit + API officielle. *(M)* — **✅ Fait (rate-limit) — PR #173.** Délai minimum global entre envois (verrou Redis distribué), couvre diffusions et envois individuels. Testé uniquement avec Redis mocké, aucun envoi réel. `services/auth/comptes/whatsapp_client.py` (copie indépendante) non couvert. La migration vers l'API officielle reste une décision assumée de ne pas migrer (coût RAM Chromium jugé acceptable) — pas un oubli.
- [x] **Dockerfile frontend durci** (digest SHA + `USER` non‑root). *(S)* — **✅ Fait — PR #144.** Images de base épinglées au digest, nginx en non-root.
- [x] **TLS partout**. *(M)* — **✅ Fait côté backend — PR #173.** Certificat auto-signé en dev, prod alignée sur le plan CloudFront + Let's Encrypt déjà documenté. `nginx -t` validé, redirection HTTP→HTTPS testée.
- [ ] Environnement de **staging iso‑prod**. *(M)* — **Non traité — oubli de planification, pas une limite technique.** Cet item n'a été assigné à aucun des chantiers du 3 septembre ; à reprendre séparément.
- [x] **Chiffrement au repos** des PII abonné. *(M)* — **✅ Fait — PR #173.** `nom`/`prenom`/`telephone_whatsapp`/`adresse` chiffrés (Fernet, clé dédiée fail-fast). Aucun filtre `__exact`/`__icontains` existant sur ces champs — pas de régression.

### 🟢 P3 — Évolution (métier avancé, conformité, documentation)

**O. Métier avancé**

- [ ] **Tarification par tranches progressives**. *(L)* — **❓ Non revérifié dans cette passe**, présumé inchangé depuis juillet.
- [x] **Report d'arriérés** inter‑périodes. *(M)* — **✅ Fait — PR #130 (2026-08-27).** « saisir les dettes antérieures et les reporter sur la facture suivante » — facture de régularisation, imputation du plus ancien au plus récent.
- [ ] **Pénalités de retard**. *(M)* — **❓ Non revérifié dans cette passe.**
- [ ] **Estimation automatique** des compteurs non relevés. *(M)* — **❓ Non revérifié dans cette passe.**
- [ ] **Multi‑agence / multi‑tenant**. *(L)* — **❓ Non revérifié dans cette passe.**
- [ ] **Retry automatique** des notifications en échec. *(S)* — **Toujours non fait** (0 occurrence de `retry` dans `services/notification/notifications/*.py`).

**P. Conformité données & légal**

- [x] **RGPD** : export + effacement/anonymisation. *(M)* — **✅ Fait — PR #179 (fusionnée 03/09).** Nouvelles RPC `ExporterDonneesAbonne` (droit à la portabilité — agrège identité déchiffrée + campagnes/factures/paiements/notifications, dégradation gracieuse **par section** si un service tiers est indisponible) et `AnonymiserAbonne` (droit à l'effacement — uniquement sur un abonné déjà `RESILIE`, remplace nom/prénom/téléphone/adresse par des valeurs placeholder explicites, jamais les factures/paiements — obligation de conservation légale). Mutations gateway ADMIN uniquement. `abonne` 85→107 tests, `gateway` 273→277. **Non couvert** : complétude juridique des champs exportés et durée de conservation avant purge, non validées par un juriste — mécanisme technique seulement.
- [ ] Politique de **rétention des données**. *(S)* — **❓ Non revérifié dans cette passe.**
- [ ] **CGU / politique de confidentialité**. *(S)* — **❓ Non revérifié dans cette passe.**

**Q. Documentation**

- [x] 🔗 **Réaligner** `CLAUDE.md`/`docs/` sur la réalité. *(M)* — **✅ Fait, de façon exemplaire.** Au moins 4 encadrés « ⚠️ Corrigé le [date] » (28/08, 31/08, 1er/09) dans `CLAUDE.md`, chacun daté et mesuré précisément (ex. « 0 occurrence contre 54 » pour Signal Forms) ; PR #135 (29/08) « docs: cible AWS, chaîne de livraison, et contexte aligné » ; PR #97 (19/07) a créé `MEMORY.md`/`CONTEXT.md`/`AUDIT_SGFE.md` eux-mêmes. Paradoxe relevé : ce travail de réalignement a touché `CLAUDE.md` en continu, mais pas `AUDIT_SGFE.md`/`ETAT_DU_SYSTEME.md` eux-mêmes — d'où cette revue du 3 septembre.
- [x] **Régression du proxy de dev local après durcissement TLS.** *(S)* — **✅ Fait — PR #180 (backend, doc) + #146 (frontend, fonctionnel), fusionnées 03/09.** Le durcissement TLS de nginx (PR #173) redirige tout `:80` vers `:443` sans port explicite dans `$host` — `proxy.conf.json` (`http://localhost:8080`) et plusieurs docs (`CLAUDE.md` des deux dépôts, `ARCHITECTURE.md`, e2e/loadtest) n'avaient jamais été mis à jour, cassant silencieusement `/graphql` en local depuis PR #173. Corrigé vers `https://localhost:8443` + certificat auto-signé, découvert et vérifié en testant manuellement PR #145 (pagination) en navigateur contre la stack reconstruite.
- [x] **Runbook d'exploitation**. *(M)* — **✅ Fait — PR #176 (fusionnée 03/09).** `docs/RUNBOOK.md` : diagnostic rapide (état des 21 conteneurs, sondes nginx/whatsapp-service), incidents par symptôme (service en boucle de redémarrage, secret manquant, mTLS silencieusement replié en clair, base inaccessible, cron arrêté), procédure de restauration réelle, rollback applicatif et de migration pas-à-pas. Rédigé en inspectant le code réel plutôt qu'en supposant le comportement — a d'ailleurs découvert au passage le bug de redélivraison Redis Streams bloquée corrigé par PR #177 (voir §F).
- [x] Mettre à jour `docs/ETAT_DU_SYSTEME.md` (Reporting implémenté). *(S)* — **✅ Fait — dans le cadre de cette même revue du 3 septembre 2026** (voir `docs/ETAT_DU_SYSTEME.md`, note de fraîcheur en tête de document).
- [x] **Plan de reprise d'activité (DR)**. *(M)* — **✅ Fait — PR #178 (fusionnée 03/09).** `docs/PLAN_REPRISE_ACTIVITE.md` (447 lignes). **Écart opérationnel réel trouvé, corrigé le 04/09** : `ansible/01-infra.yml` provisionne un bucket S3 de sauvegarde (chiffré, versionné, IAM déjà en place — `politique-instance.json.j2` autorisait déjà `s3:PutObject`, jamais exercé) que `scripts/backup-databases.sh` n'envoyait jamais dessus — le RPO annoncé de ~24h était en réalité infini en cas de perte totale de l'instance EC2. Corrigé : upload optionnel (`AWS_BACKUP_BUCKET`, vide en dev = comportement inchangé, obligatoire `:?` en prod), `aws-cli` ajouté à l'entrypoint prod de `db-backup`. Testé en direct sur la pile partagée : chemin sans variable inchangé (8/8 dumps OK), chemin avec variable dégrade proprement (dump local conservé, échec S3 signalé, code de sortie non nul).
- [ ] i18n : externaliser les messages backend codés en dur. *(M)* — **Toujours non fait, mais convention assumée.** Cohérent avec la règle `CLAUDE.md` elle-même (commentaires et messages métier en français, code en anglais) — jamais présenté comme un manque à combler plutôt qu'un choix de convention.

### Portes de sortie (Go / No‑Go) — statut au 3 septembre 2026 (3e passe, tout fusionné)

> Mise à jour : les 10 PR de la 2e passe (backend #168-174, frontend #142-144) sont désormais **toutes fusionnées** dans `develop`, ainsi que 9 PR supplémentaires ouvertes le même jour (backend #175-183, frontend #145-146) — dead-letter Redis Streams, runbook, plan de reprise d'activité, RGPD, correctif du proxy de dev local, mypy strict (9/9, CI câblée), pagination UI.

| Porte | Condition | Items requis | Statut |
|---|---|---|---|
| 🚦 **Go production (technique)** | Aucun secret exposé, périmètre gRPC verrouillé, système déployable + sauvegardé, bugs bloquants corrigés | **Tous les P0** + F, I (min. `/metrics` + healthchecks + logs) + K (e2e smoke vert) | 🟡 **Toujours bloqué sur un seul point.** P0 à 26/27 (seul reste : rotation Brevo, hors code) — mTLS vérifié en direct, healthchecks complets sur les 9 composants. **I (observabilité, hors healthchecks) reste à zéro, jamais entamé** — bloque toujours cette porte à elle seule, `/metrics`/`TracerProvider`/logs structurés absents. |
| 🔗 **Complètement aligné** | Front ⇄ back sans écart de contrat ni dérive | Tous les items **🔗** : D, E‑proxy, H, K‑e2e, L‑codegen/typeEnvoi/index, Q‑doc | ✅ **Quasi complet.** L‑index (Decimal) fait (PR #171) ; le proxy de dev local re-fonctionne après la régression TLS (PR #180/#146) ; seul K‑e2e reste partiel (paiement volontairement jamais exécuté, à raison). |
| 🛡️ **Prêt pour l'audit SOC 2 Type II** | Contrôles en place **et** preuves accumulées sur la période d'observation | P0 (sécurité) + J (audit trail) + I (monitoring) + K (pentest) + rate limiting (C) | ⛔ **Toujours loin, sans surprise.** mTLS + PII + TLS + RGPD traités, mais **J et I restent à zéro, jamais entamés**, K‑pentest non fait — ce sont les vrais blocages, inchangés depuis la 1ère passe du 3 septembre. |
| 💰 **Complet fonctionnellement** | Cycle correctif financier + portail abonné opérationnels | G + H + M (pagination) | ✅ **Complet, des deux côtés.** G et H déjà faits ; **M (pagination) fait serveur (PR #172) ET UI (PR #145)** — `abonnes-list`/`factures-list` consomment réellement `limit`/`offset`, `paiements-list` non migrée par choix documenté. |

### Décompte — mis à jour le 3 septembre 2026 (3e passe, tout fusionné dans `develop`)

> Contrairement aux deux passes précédentes de cette même journée (qui comptaient un item « ✅ Fait » dès le code écrit/testé/committé, PR ouverte ou non), **cette passe ne compte fait que ce qui est réellement fusionné dans `develop`** — la quasi-totalité des PR alors ouvertes (#142, #143, #168 à #174) l'ont été depuis, plus 8 PR supplémentaires ouvertes et fusionnées dans la foulée (#175 à #181 côté backend, #145/#146 côté frontend). Voir §8 pour le détail et les preuves, PR par PR.

| Priorité | Total | ✅ Fait | 🟡 Partiel | ❓ Incertain / non revérifié | Non fait | Effort dominant (origine) |
|---|:---:|:---:|:---:|:---:|:---:|---|
| 🔴 P0 | 27 | 26 | 0 | 1 | 0 | S/M (+ 2 L : mTLS fait — PR #168, déploiement fait) |
| 🟠 P1 | 33 | 19 | 2 | 0 | 12 | M (+ 2 L : outbox non fait par choix, avoir/rectification fait) |
| 🟡 P2 | 21 | 20 | 0 | 0 | 1 | S/M (+ 1 L : réplication PostgreSQL, fait en PoC — PR #173) |
| 🟢 P3 | 15 | 7 | 0 | 6 | 2 | M/L |
| **Total** | **96** | **72 (75 %)** | **2 (2 %)** | **7 (7 %)** | **15 (16 %)** | — |

> **Progression de la journée** : 0→37→48→50→64→**70** items faits au fil du 3 septembre 2026. Le total est passé de 94 à **96** items (2 ajouts : un bug réel de redélivraison Redis Streams découvert et corrigé en rédigeant le runbook — PR #177 — et la régression du proxy de dev local causée par le durcissement TLS — PR #180/#146). Trois nouveaux items complétés depuis la 2e passe, en plus des PR alors ouvertes désormais fusionnées : **RGPD export/anonymisation** (PR #179), **plan de reprise d'activité** (PR #178, avec un vrai écart opérationnel trouvé et depuis corrigé le 04/09 — sauvegardes désormais envoyées vers le bucket S3 provisionné), et **consommation de la pagination côté UI** (PR #145 frontend, en plus du serveur déjà fait).
>
> **`mypy --strict`** (P2, `§L`) a nettement progressé sans être fini : **6 des 9 composants backend à 0 erreur** (auth, abonne, reporting, notification, paiement, gateway — PR #181), **3 restants en cours** (campagne, config, facturation) sur une branche non encore fusionnée à la rédaction de cette ligne. Câblage CI volontairement différé tant que les 9 ne sont pas tous propres.
>
> **Ce qui reste, par choix explicite** : observabilité (§I) et piste d'audit (§J) — chantiers à part entière, jamais entamés. Tarification par tranches, pénalités de retard et estimation automatique des compteurs — **déclinés explicitement par l'utilisateur** le 3 septembre (staging iso-prod, CGU et multi-tenant classés « pas d'actualité », pas refusés). **Ce qui reste, P1 « critique »** : uniquement des items déjà hors périmètre pour une raison précise (voir liste détaillée dans la note de la 1ère passe, inchangée) ou dépendant de l'observabilité. **Ce qui reste, P2** : uniquement l'environnement de staging iso-prod (non fait, explicitement pas d'actualité pour l'instant) — le code vestigial et `mypy --strict` (9/9, CI câblée) sont désormais faits.
>
> **Points d'attention restants** : Trivy (PR #143, fusionnée) a bien fait échouer la CI frontend comme prévu au premier run (`nginx:1.27-alpine`) — non re-vérifié si le tag a depuis été bumpé. Les 191 événements Redis Streams bloqués ont été purgés le 04/09 (voir §F) — plus un point d'attention.

---

## 9. Conclusion

Le SGFE est un projet **techniquement impressionnant** : conception microservices soignée, backend rigoureux (surtout sur l'argent et la concurrence), frontend Angular à l'état de l'art, et une usine logicielle (CI/supply‑chain) au‑dessus de la moyenne. Ces fondations sont un **véritable atout** et méritent d'être préservées.

L'écart à combler n'est pas dans la *conception* mais dans la **robustesse en production et la posture de sécurité distribuée** : verrouiller la couche gRPC, externaliser les secrets (à commencer par la clé Brevo, **dès aujourd'hui**), livrer un déploiement backend avec sauvegardes, instaurer une piste d'audit et de l'observabilité, corriger deux bugs concrets, et compléter le volet financier correctif ainsi que le portail abonné. Aucun de ces points n'est hors de portée ; ensemble, ils font passer le système d'un **excellent prototype avancé** à un **produit exploitable et auditable**.

En traitant les huit actions **P0**, le projet devient déployable de façon responsable ; en enchaînant sur **P1**, il se rapproche des exigences d'un audit SOC 2 Type II (moyennant la période d'observation requise).

---

## 10. Décisions d'exécution & plan cadré (Local maintenant → Azure moyen terme → Kubernetes en cible)

> ⚠️ **L'horizon ② a changé de cible le 28 août 2026 : AWS, plus Azure.**
> Les décisions *applicatives* de cette section restent valables — elles sont
> indépendantes de l'infrastructure, c'est d'ailleurs ce que la section dit
> elle-même. Seules les mentions de plateforme sont périmées :
>
> | Écrit ici | Cible retenue |
> |---|---|
> | Azure Key Vault | AWS Secrets Manager |
> | Flexible Server + PITR | RDS PostgreSQL, ou conteneurs + snapshots — arbitrage ouvert |
> | Front Door / App Gateway WAF | CloudFront |
> | Identité managée | Profil d'instance EC2 (rôle IAM) |
> | AKS + Ansible en ③ | Ansible dès ②, pour l'infrastructure et l'amorçage — **pas** dans la boucle de livraison |
>
> Le détail se trouve dans [`docs/INFRASTRUCTURE_AWS.md`](docs/INFRASTRUCTURE_AWS.md)
> (dimensionnement, coûts, réseau) et [`docs/CHAINE_DE_LIVRAISON.md`](docs/CHAINE_DE_LIVRAISON.md)
> (qui déploie quoi, et dans quel ordre).

Cette section fige les décisions prises lors du cadrage. **Trois horizons de déploiement :** **① Local (maintenant)** — Docker Compose en local (dév) ; **② Azure (moyen terme)** — migration cloud, d'abord Docker Compose sur VM Azure ; **③ Kubernetes/AKS + Ansible (cible)**. **Point clé : l'essentiel du travail P0/P1 est indépendant de l'infrastructure** (correctifs de code, sécurité applicative, tests, volet financier, espace abonné) et se fait **dès maintenant en local** ; seuls les items « plateforme » (Key Vault, bases & observabilité managées, WAF, mesh) attendent la migration Azure. Les décisions *(reco)* sont adoptées par défaut — **modifiables sur simple demande**.

> **Exécution constatée au 3 septembre 2026** (détail sourcé en §8) : les décisions **10.1 (P0)** sont exécutées à 19/27, **10.2 (P1)** sont largement exécutées pour F/G/H (robustesse, financier, espace abonné), mais **I (observabilité)** et **J (piste d'audit, §10.7)** — les deux items les plus structurants de 10.2 — **restent à l'état de décision, non implémentées**. **10.3 (P2)** a été partiellement exécutée côté frontend (strict TS, codegen) lors de la refonte du 27 août, peu côté backend (pagination, lib gRPC partagée toujours ouvertes). **10.4 (P3)** : seul l'item arriérés a été livré.

### 10.1 Décisions Phase P0

| Sous-partie | Décision | Note |
|---|---|---|
| **E — Déploiement** | **① Local (maintenant)** → **② Azure/VM (moyen terme)** → **③ AKS + Ansible (cible)** | Aujourd'hui : Docker Compose **en local**. Les notes ci‑dessous indiquent l'horizon d'application |
| **B — gRPC/réseau** | **Isolation réseau + jeton d'identité** inter‑services | **① Local (dès maintenant) :** réseau Docker interne, **ne pas publier** les ports gRPC/DB sur l'hôte + jeton signé par la gateway vérifié par chaque service. **② Azure :** + NSG. **③ k8s :** `NetworkPolicies` puis mTLS mesh (Linkerd) |
| **D — CHÈQUE** | **Retirer `CHEQUE` du frontend** | Type `ModePaiement` (`facture.model.ts:4`) + 3 sélecteurs ; backend inchangé |
| **A — Secrets** | **`.env` gitignoré (local) → Azure Key Vault (à la migration)** *(reco)* | **① Local :** `.env` gitignoré convient au dév, mais **rotationner la clé Brevo + le mot de passe admin** (credentials réelles) et ne rien committer. **② Azure :** Key Vault + Docker secrets via identité managée / `ansible-vault`. **③ k8s :** CSI Driver + Workload Identity. Supersède la mention SOPS de la §8·A |
| **A — JWT** | **RS256** (asymétrique, signé par auth‑service) | Traité sans re‑questionnement |
| **C — Edge** | **Rate limiting + durcissement GraphQL** | **Désactivation introspection/GraphiQL + limites de profondeur/complexité = dès maintenant (local).** Rate limiting : **① Local/Azure‑VM :** nginx (`limit_req`). **② Azure :** + Front Door/App Gateway WAF. **③ k8s :** AGIC + App Gateway WAF |
| **D — SUPERVISEUR** | Ajout de `created_by` à `CampagneResponse` + type gateway | Correctif clair |

> **Réponse à votre question secrets :** **en local (situation actuelle), un `.env` gitignoré est acceptable pour le développement** — mais ce n'est pas un mode de production (pas de chiffrement au repos, pas de rotation, pas d'audit d'accès), et la clé Brevo présente est une **credential réelle** : **rotationnez‑la maintenant** et gardez `.env` hors de Git. **À la migration Azure (②) :** **Azure Key Vault** devient la source de vérité — la VM le lit via son **identité managée** (ou `ansible-vault`) et injecte les valeurs en **Docker secrets** (rien dans l'image/Git). **En Kubernetes (③) :** **Secrets Store CSI Driver** + **Workload Identity** + **chiffrement etcd**.

### 10.2 Décisions Phase P1 *(options recommandées adoptées)*

| Sous-partie | Décision |
|---|---|
| **F — Robustesse distribuée** | **Création paresseuse du solde + commande de réconciliation** (supprime vite la facture orpheline ; outbox = cible d'évolution) |
| **G — Volet financier** | **Avoir comptable + annulation/remboursement de paiement + reçu PDF** |
| **H — Espace abonné** | **Consultation seule** (facture/solde/historique par token + états invalide/expiré) ; paiement en ligne reporté |
| **I — Observabilité** | **Instrumenter OpenTelemetry dès maintenant** (constant, réutilisable) → **① Local :** exporter vers Jaeger/Prometheus/Grafana en compose (ou console). **② Azure :** Application Insights + Azure Monitor. **③ k8s :** Container Insights + **Managed Prometheus/Grafana** |
| **J — Piste d'audit** | **Table d'audit *append‑only*** (qui/quoi/quand) + **logs structurés `trace_id`** (dimensionnement selon objectif SOC 2 — *à confirmer*) |
| **K — Tests** | **Parcours critiques d'abord** (file offline terrain, refresh de session, paiement), puis montée vers **~70 %** ; **e2e Playwright réels** en CI |

### 10.3 Décisions Phase P2 *(recommandé)*

- **L — Qualité & alignement fin :** activer `strict` TypeScript + `strictTemplates` ; **graphql‑codegen** ; `mypy --strict` en CI ; lib partagée `sgfe_common.grpc` ; unifier l'index compteur en `Decimal` ; retirer le code vestigial (`grpc_code`, resolver `dashboard`, réactiver `typeEnvoi`) ; ajouter `isSuperviseur`.
- **M — Performance :** pagination `limit/offset` + recherche sur les listes ; index DB ciblés.
- **N — Résilience :** **① Local :** PostgreSQL/Redis en conteneurs (comme aujourd'hui) + `pg_dump` régulier si données réelles. **② À la migration Azure :** basculer vers **Azure Database for PostgreSQL Flexible Server** (HA + backups + PITR) et **Azure Cache for Redis**. **WhatsApp :** conserver `whatsapp-web.js` court terme avec **file d'attente + rate‑limit**, planifier la migration vers l'**API officielle WhatsApp Business** (fiabilité/anti‑bannissement). Dockerfile frontend durci + limites de ressources + `restart` = dès maintenant.

### 10.4 Décisions Phase P3 *(recommandé)*

- **O — Métier avancé :** **planifier la tarification par tranches progressives** + redevance/taxes (standard eau) ; report d'arriérés ; pénalités de retard ; estimation des compteurs non relevés.
- **P — Conformité données :** socle **RGPD** (export + anonymisation à la résiliation) ; politique de rétention.
- **Q — Documentation :** **réaligner `CLAUDE.md`/`docs/` sur la réalité et sur Azure** ; runbook d'exploitation ; plan de reprise (DR).

### 10.5 Cibles Azure — à partir de la migration (moyen terme), révision des §7‑§8

*Ces équivalents managés s'appliquent **à partir de la migration Azure** — **en local (①) on reste sur les versions simples** (colonne du milieu). « **Azure** » = dès la VM Azure ; « **AKS** » = avec Kubernetes.*

| Besoin | En local (maintenant) | Cible Azure / AKS (à la migration) |
|---|---|---|
| Secrets | `.env` gitignoré | **Azure Key Vault** + Docker secrets/Ansible (Azure) → **CSI + Workload Identity** (AKS) |
| Observabilité | Jaeger/Prometheus/Grafana en compose | **Application Insights + Azure Monitor** (Azure) → **Managed Prometheus/Grafana** (AKS) |
| Bases PostgreSQL (×8) | Conteneurs + `pg_dump` | **Azure Database for PostgreSQL Flexible Server** (HA, PITR, backups gérés) — **Azure** |
| Redis | Conteneur | **Azure Cache for Redis** (réplication/persistance managées) — **Azure** |
| Ingress / WAF / rate limiting | nginx + `limit_req` | **+ Azure Front Door / App Gateway WAF** (Azure) → **AGIC + App Gateway WAF** (AKS) |
| Registre d'images | GHCR | **Azure Container Registry (ACR)** + **Defender for Containers** (scan) |
| TLS | Let's Encrypt manuel | **cert‑manager** (ou certificats Key Vault sur App Gateway) |
| mTLS interne | isolation réseau + jeton d'identité | **Linkerd/Istio** sur AKS (AKS) |
| Posture sécurité (SOC 2) | — | **Microsoft Defender for Cloud** (recommandations CIS/monitoring) |
| IaC | Ansible | **Ansible** retenu (collection `azure.azcollection`) ; *option : Bicep/Terraform pour le provisioning, Ansible pour la config applicative* |

### 10.6 Prochaine étape — implémentation des quick wins P0

Prêts à être implémentés immédiatement (petits correctifs à fort impact), sur une **branche Git dédiée par chantier** pour relecture avant merge :

1. **Bug SUPERVISEUR** — `created_by` dans `CampagneResponse` (proto + serializer + type gateway) + régénération des stubs.
2. **Retrait de CHÈQUE** côté frontend (type + 3 sélecteurs).
3. **Config sûre** — `DEBUG=False` par défaut, échec au démarrage si `SECRET_KEY`/`JWT_SECRET_KEY` absents, cookie refresh `Secure=True`, nettoyage config morte.
4. **JWT RS256** — génération de la paire de clés, signature côté auth, validation par clé publique.
5. **Durcissement GraphQL** — introspection/GraphiQL désactivés hors dev + limite de profondeur/complexité.
6. **whatsapp‑service fail‑closed** + `/health` renvoyant 503 si déconnecté.
7. **En‑têtes de sécurité** (SecurityMiddleware, HSTS, CSP) au niveau gateway/ingress.

> Dites simplement **« go »** (et confirmez : branche Git dans vos dépôts, ou diffs à relire ici) et j'attaque ces correctifs. Les chantiers lourds (mTLS, Key Vault/CSI, Flexible Server, outbox, avoir/remboursement, observabilité) feront chacun l'objet d'une spec/ADR avant implémentation.

### 10.7 Conception — propagation d'identité → journal d'audit immuable (dernier P0)

> **Statut (juillet 2026)** : les 7 quick wins §10.6 **et** tout le volet « P0 sans Azure » (isolation réseau, rate limiting, sauvegardes PostgreSQL, cible de déploiement, espace abonné, + access token court, retry‑401 REST, garde‑fou `.env`, nettoyage bcrypt) sont **livrés et mergés**. Le **dernier item P0** est ce couple **propagation d'identité → journal d'audit** (CC7.2/CC7.3 SOC 2, « qui a fait quoi »). Cette section fige la conception ; rien n'est encore implémenté.

**État des lieux (code réel).**

- La gateway **connaît** déjà l'appelant : `require_auth`/`require_role` (`gateway/schema/context.py:89‑107`) valident le JWT via `auth_client.validate_token` et renvoient un `UserPayload` (`user_id`, `username`, `role`).
- Mais le « qui » est propagé **au cas par cas dans les messages** de requête : `created_by` (campagne), `caller_id` (`deactivate_user`), `auteur_id/username/role` (`SaisirIndex`/`CorrigerReleve`). Aucune métadonnée gRPC n'est posée aujourd'hui (`grpc_clients.py`).
- Un **embryon d'audit** existe : `ReleveAudit` (action / auteur / horodatage) embarqué dans `ReleveResponse` et stocké avec le relevé dans la base **campagne**. Seuls les relevés sont audités (§5.2).
- Les 8 services enregistrent déjà un intercepteur serveur uniforme (`ErrorHandlingInterceptor`) dans `grpc.server(interceptors=[…])` — **le point d'extension existe partout**.

**Décision 1 — où poser le journal : une table `audit_log` append‑only *par service*, écrite dans la *même transaction* que le changement métier.** Pas de service d'audit central comme magasin d'écriture.

- *Rationale* : la valeur d'un journal immuable est que **tout changement d'état ait son entrée, sans perte ni fantôme**. En « une base par service » sans transaction distribuée, seule l'écriture **dans la même base/transaction** garantit l'atomicité audit ↔ changement. Un service central reçoit l'événement par le réseau (deux commits) → risque de divergence, sauf pattern *outbox*… qui écrit en local d'abord de toute façon.
- *Compromis assumé* : « tout ce qu'a fait X » demande d'interroger 8 bases. On ne centralise **pas** maintenant ; si le besoin de requêtes transverses arrive, on ajoute **plus tard** une agrégation en lecture côté `reporting-service` (qui agrège déjà des stats cross‑service). Écriture locale (intégrité) maintenant, lecture centrale (confort) plus tard, jamais d'écriture centrale.

**Décision 2 — propagation d'identité : uniforme via métadonnées gRPC** (et non plus des champs ad‑hoc). La gateway attache `x-user-id` / `x-user-name` / `x-user-role` (+ `x-request-id` pour corréler) sur **chaque** appel sortant ; chaque service gagne un `IdentityInterceptor` (serveur) **posé à côté du `ErrorHandlingInterceptor`** qui range l'identité dans un `contextvar` lu au moment d'écrire l'audit. Aucun champ identité n'est ajouté aux messages.

**Note de confiance.** Les services **font confiance à la gateway** (ils ne revalident pas le JWT). Le « qui » audité n'est fiable que parce que (a) seule la gateway atteint les services (**isolation réseau — déjà faite**) et (b) le **mTLS** (différé, cible Azure) liera cryptographiquement « l'appelant = la gateway ». L'intégrité de l'audit dépend de ce canal.

**Immuabilité.** Applicatif : **INSERT seulement**. Base (défense en profondeur) : migration qui **REVOKE UPDATE/DELETE** sur la table pour le rôle du service. Option anti‑altération (P2) : **chaînage de hash** (chaque ligne hache la précédente) → suppression/édition détectable. Non requis pour la v1.

**Périmètre d'audit** : les **écritures**, jamais les lectures — par ordre de sensibilité : paiement → facturation → relevés → abonné → comptes → tarifs/config.

**Découpage (une branche par étape).**

| # | Étape | Portée |
|---|-------|--------|
| 1 | **Plomberie identité** (métadonnées + `IdentityInterceptor` par service + accès contexte) | Aucun changement fonctionnel — préalable, passe **en premier** |
| 2 | **`audit_log` + écriture sur mutation, une PR par service** | Commencer par **paiement** puis **facturation** ; puis campagne (fusionner `ReleveAudit`), abonné, auth, config |
| 3 | **Immuabilité niveau base** (REVOKE) + option chaînage | Défense en profondeur |
| 4 | **Agrégation reporting + API GraphQL de lecture (ADMIN)** | Confort de requête transverse — *plus tard* |

**Étape 1 détaillée — fichiers & tests (à implémenter, non fait).**

*Gateway :*
- `gateway/schema/identity_context.py` **(nouveau)** — un `ContextVar` `current_identity` + `set_identity(user_id, username, role)` / `get_identity()`.
- `gateway/schema/context.py` — dans `require_auth`, après `validate_token`, appeler `set_identity(...)` (une seule ligne ; l'identité vaut pour la durée de la requête).
- `gateway/schema/grpc_clients.py` — un `IdentityClientInterceptor(grpc.UnaryUnaryClientInterceptor)` qui lit `get_identity()` et **ajoute les métadonnées** `x-user-id/name/role` (+ `x-request-id`) ; envelopper chaque canal : `grpc.intercept_channel(channel, IdentityClientInterceptor())` dans chaque `__init__` de client (ou une fabrique commune). Appel **anonyme** (identité absente : login, espace abonné public) → aucune métadonnée, comportement normal.

*Chaque service (patron copié, comme `ErrorHandlingInterceptor`) :*
- `services/<svc>/<app>/grpc_interceptors.py` — ajouter `IdentityInterceptor(grpc.ServerInterceptor)` : lit les métadonnées de `handler_call_details`, pose un `contextvar` `caller_identity` autour de l'appel, le remet à zéro ensuite ; + un accesseur `get_caller()`.
- `services/<svc>/<app>/grpc_server.py` — `interceptors=[ErrorHandlingInterceptor(), IdentityInterceptor()]`.

*Tests :*
- Gateway : `IdentityClientInterceptor` — contextvar peuplé ⇒ métadonnées présentes ; contextvar vide ⇒ appel sans métadonnées (anonyme).
- Service : `IdentityInterceptor` — métadonnées présentes ⇒ `get_caller()` renvoie l'identité ; absentes ⇒ identité vide.
- Intégration légère (réutiliser le test SUPERVISEUR existant campagne↔gateway) : asserter que le service **reçoit** l'identité.

*Points de vigilance :* (1) `ContextVar` à travers le pool de threads gRPC de la gateway — l'appel part dans le thread du resolver, propagation OK, **à valider par un test** ; (2) **rétro‑compat** : les champs explicites existants (`created_by`, `auteur_*`, `caller_id`) restent en place — ils migreront vers le mécanisme uniforme dans les PR d'étape 2, pas maintenant (zéro régression) ; (3) sync (WSGI, queries/mutations) **et** async (ASGI, subscriptions) : vérifier la propagation dans les deux.

---

## Annexe — Méthodologie & périmètre

- **Sources :** code réel des deux dépôts, rapatrié et analysé hors ligne (backend 559 fichiers / frontend 451 fichiers, hors `node_modules`, `.venv`, `.git`, artefacts de build).
- **Approche :** six analyses spécialisées en parallèle (sécurité/SOC 2, qualité backend, qualité frontend, alignement des contrats API, complétude fonctionnelle, infra/DevOps), puis vérification manuelle des affirmations les plus fortes directement dans le code (bug SUPERVISEUR, gRPC en clair, secrets, mode CHÈQUE, défauts de configuration, observabilité).
- **Principe :** priorité au **code réel** sur la documentation, chaque constat étant rattaché à un `fichier:ligne`.
- **Limites :** l'historique Git n'a pas été analysé (dépôts fournis sans `.git`) — la vérification « les secrets ont‑ils été committés ? » reste à faire côté équipe ; l'analyse est statique (pas d'exécution ni de test de pénétration dynamique) ; la couverture de tests est estimée à partir du volume et des seuils CI, non exécutée.
- **Note de confidentialité :** les valeurs de secrets réels rencontrées ne sont pas reproduites dans ce rapport. La clé Brevo et le mot de passe admin ayant été observés lors de l'analyse, leur rotation est recommandée par précaution, indépendamment du reste.
