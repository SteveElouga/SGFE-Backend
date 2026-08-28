# Bilan d'audit technique — Système de Gestion de Facturation d'Eau (SGFE)

**Périmètre audité :** dépôts `SGFE-backend` (microservices Django + gRPC, gateway GraphQL, service WhatsApp Node.js) et `SGFE-frontend` (Angular 22 PWA).
**Date :** 17 juillet 2026
**Méthode :** analyse du **code réellement livré** (368 fichiers Python, 133 fichiers TypeScript, 8 fichiers `.proto`, configs Docker/CI). La documentation interne (`CLAUDE.md`, `docs/`) décrit une architecture *cible* qui diverge parfois du code ; toutes les conclusions ci‑dessous s'appuient sur le code réel, avec références `fichier:ligne` vérifiables.
**Public :** ce document combine une **synthèse pour décideurs** (sections 1‑2) et un **détail technique actionnable** (sections 3‑8).

---

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

- **Prêt pour la production ?** → **Non**, pas en l'état. Fondations logicielles excellentes, mais la moitié « exploitation » manque (déploiement backend, observabilité, sauvegardes, redondance) et un secret tiers réel est exposé.
- **Conforme SOC 2 ?** → **Non.** La culture sécurité est réelle (auth soignée, CI durcie) mais les contrôles d'accès inter‑services, la gestion des secrets et surtout la **piste d'audit / journalisation** — indispensable à un Type II — sont absents.
- **Le front et le back fonctionnent‑ils ensemble ?** → **Oui pour ADMIN/AGENT/COMPTABLE.** Le rôle **SUPERVISEUR est cassé** par un bug backend trivial à corriger.

### Top 5 des risques à traiter en priorité

1. **Clé API Brevo réelle (live) en clair** dans `services/auth/.env` (+ mot de passe admin par défaut). → Révoquer/rotationner **immédiatement**.
2. **gRPC inter‑services en clair et non authentifié** : quiconque atteint le réseau interne peut créer un admin, solder des factures, exfiltrer la base abonnés. → mTLS + isolation réseau.
3. **Aucun déploiement backend ni sauvegarde** : le cœur métier n'a pas de cible de mise en production ni de protection des données. → Manifeste de déploiement + backups PostgreSQL.
4. **Rôle SUPERVISEUR non fonctionnel** (`AttributeError` sur `campagne.created_by`). → Correctif de quelques lignes.
5. **Pas de journal d'audit ni d'observabilité** : impossible de savoir « qui a fait quoi » ni de diagnostiquer un incident. → Audit trail + logs structurés + `/metrics`.

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

- [ ] Révoquer et régénérer la **clé API Brevo** (`services/auth/.env:31`) et le mot de passe admin `Admin1234!` (`.env:43`). *(S)*
- [ ] Vérifier que les `.env` n'ont **jamais** été committés : `git log --all -- "**/.env"` et `git ls-files | grep -i env`. Si oui → purge d'historique (BFG / `git filter-repo`) + rotation de tous les secrets exposés. *(S)*
- [ ] Externaliser **tous** les secrets vers un coffre (Vault / Secrets Manager cloud / K8s Secrets chiffrés SOPS) et retirer les valeurs en dur de `docker-compose.yml` (`JWT_SECRET_KEY`, `DJANGO_SECRET_KEY`, `POSTGRES_PASSWORD`, l.62‑71). *(M)*
- [ ] Supprimer toutes les **valeurs par défaut** de secrets dans les `settings.py` ; **échec au démarrage** si `DJANGO_SECRET_KEY`/`JWT_SECRET_KEY` absents (`services/auth/auth/settings.py:15,99`). *(S)*
- [ ] `DEBUG=False` par défaut partout (`settings.py:16` + `env.example`). *(S)*
- [ ] Migrer le JWT en **RS256 asymétrique** : seul `auth-service` détient la clé privée ; gateway et services valident avec la clé publique. *(M)*
- [ ] Nettoyer la config morte/trompeuse : `BCRYPT_ROUNDS` sans `PASSWORD_HASHERS`, `JWT_SECRET_KEY="changeme"` vestigial dans paiement/campagne/facturation. *(S)*

**B. Sécurité inter‑services & réseau**

- [ ] Activer **mTLS** entre tous les services gRPC (`add_secure_port` + credentials) ou déployer un service mesh (Linkerd/Istio). *(L)*
- [ ] Propager un **jeton d'identité signé par la gateway** dans les métadonnées gRPC + intercepteur d'**authz côté chaque service** (fin de la confiance aveugle envers la gateway). *(L)*
- [ ] **Isolation réseau** : ne plus publier les ports gRPC `50051‑50058` ni PostgreSQL `5432‑5439` sur l'hôte ; réseaux Docker cloisonnés (edge/app/data) ; en K8s, `NetworkPolicies`. *(M)*
- [ ] whatsapp‑service **fail‑closed** : refuser le démarrage / toute requête si `WHATSAPP_INTERNAL_API_KEY` absente (`whatsapp-service/server.js:35`). *(S)*

**C. Durcissement des accès (edge)**

- [ ] **Rate limiting** sur login / refresh / OTP / reset (par IP **et** par compte) — DRF throttle ou `limit_req` nginx. *(M)*
- [ ] Désactiver **introspection + GraphiQL** hors dev (`gateway/gateway/urls.py:11`). *(S)*
- [ ] Ajouter **limites de profondeur / complexité / coût** des requêtes GraphQL + plafond de batching (`gateway/schema/schema.py`). *(M)*
- [ ] Réduire l'**access token** à 15‑30 min (`services/auth/auth/settings.py:101`). *(S)*
- [ ] Forcer le cookie refresh `Secure=True` en prod indépendamment de `DEBUG` (`gateway/gateway/settings.py:84`). *(S)*
- [ ] En‑têtes de sécurité : `SecurityMiddleware` Django + **HSTS** + **CSP** + `X-Content-Type-Options` + `Referrer-Policy` (gateway + nginx LB). *(M)*
- [ ] Confirmer l'intention de `infosSociete` non authentifié, sinon `require_auth` (`gateway/schema/config_queries.py:11`). *(S)*

**D. Bugs d'alignement bloquants**

- [ ] 🔗 **Corriger le rôle SUPERVISEUR** : ajouter `created_by` à `CampagneResponse` (`proto/campagne_service.proto:91`), le peupler dans le serializer/`grpc_server` de campagne, l'exposer dans le type `Campagne` de la gateway, régénérer les stubs (`gateway/schema/campagne_queries.py:32`). *(S)*
- [ ] 🔗 **Corriger le mode CHÈQUE** : l'ajouter à l'enum `ModePaiement` backend (`services/paiement/paiements/models.py:8`) **ou** le retirer du front (`facture.model.ts:4` + 3 sélecteurs) ; ajouter `full_clean()` dans le repo paiement. *(S)*

**E. Déploiement & protection des données**

- [ ] Créer une **cible de déploiement backend** réelle : manifestes k8s complets **ou** `docker-compose.prod.yml` backend, incluant la gateway. *(L)*
- [ ] **Pipeline CD backend** (build → push → migrate → deploy) avec **migrations pilotées** (job `migrate` avant rollout). *(M)*
- [ ] 🔗 Router `/graphql` du frontend de prod vers la **vraie gateway** (corriger l'absence de service `api-gateway` dans `frontend/docker-compose.prod.yml`). *(S)*
- [ ] **Sauvegardes PostgreSQL** automatiques (dump quotidien + archivage WAL) pour les 8 bases + backup de la session Redis WhatsApp. *(M)*
- [ ] **Tester la restauration** (restore drill documenté). *(S)*
- [ ] Chiffrer les sauvegardes. *(S)*
- [ ] Ajouter `restart: unless-stopped` + `deploy.resources.limits` (CPU/mémoire) sur tous les conteneurs. *(S)*

### 🟠 P1 — Critique (fiabilité, complétude essentielle, conformité)

**F. Robustesse distribuée**

- [ ] **Transactional outbox** : publier `FactureGeneree`/`PaiementEnregistre` dans la même transaction que l'écriture (fiabilise `initialiser_solde` et la synchro de statut). *(L)*
- [ ] Alternative/renfort : **création paresseuse** du `SoldeFacture` au 1er paiement + **commande de réconciliation** facture↔solde (supprime les factures orphelines, `facturation/services.py:209`). *(M)*
- [ ] **Clé d'idempotence** sur `EnregistrerPaiement` (`reference_transaction` unique ou `Idempotency-Key`) — anti double‑versement (`paiement/grpc_server.py:55`). *(M)*
- [ ] Rendre la **synchro de statut facture** rejouable + **recalcul périodique** du read‑model reporting (`paiement/grpc_server.py:73`). *(M)*
- [ ] **Robustesse clôture → facturation** : retry/file + régénération manuelle déclenchable si Facturation KO. *(M)*
- [ ] **Verrouiller les crons** (leader election via `pg_advisory_lock`/Redis, ou K8s `CronJob` unique) — anti relances/suspensions dupliquées (`paiement/schedulers.py:45`). *(M)*
- [ ] Logger les **exceptions avalées** (`campagne/campagnes/services.py:195`). *(S)*

**G. Volet financier correctif (complétude essentielle)**

- [ ] Facture d'**avoir / annulation / rectification** + régénération de facture après correction de relevé. *(L)*
- [ ] **Annulation / remboursement** de paiement (RPC dédié + traçabilité). *(M)*
- [ ] **Reçu de paiement (PDF)**. *(M)*
- [ ] Gestion du **trop‑perçu / crédit** abonné. *(M)*

**H. Espace abonné (complétude + alignement)**

- [ ] 🔗 Implémenter le composant **espace‑abonné** : requête par token, affichage facture/solde/historique, états token invalide/expiré (`espace-abonne.component.ts:54`). *(M)*
- [ ] 🔗 Ajouter la route `espace‑abonne` à `proxy.conf.json` (same‑origin en dev) + à la config nginx de prod. *(S)*
- [ ] (Option) **Paiement en ligne** dans l'espace abonné. *(L)*

**I. Observabilité (exploitation + SOC 2 CC7)**

- [ ] Instrumenter **OpenTelemetry** (traces) réellement + exporteur OTLP (dépendances déjà présentes, jamais câblées). *(M)*
- [ ] Exposer **`/metrics`** Prometheus sur chaque service + la gateway. *(M)*
- [ ] **Logs JSON structurés** avec `trace_id` corrélé. *(M)*
- [ ] Déployer la stack **Prometheus/Grafana/Loki/Jaeger** (ou équivalent managé). *(M)*
- [ ] **Healthchecks applicatifs** (`/health`) sur la gateway + `depends_on: service_healthy`. *(S)*
- [ ] `/health` whatsapp renvoie **503** si déconnecté (`whatsapp-service/server.js:175`). *(S)*
- [ ] **Règles d'alerte** (taux d'erreur, latence, échec facturation, cron impayés, WhatsApp déconnecté) + routage on‑call. *(M)*

**J. Piste d'audit & conformité SOC 2**

- [ ] **Journal d'audit métier immuable** (qui/quoi/quand) : création/désactivation d'utilisateur, changement de rôle, paiement, annulation, changement de tarif, suspension/réactivation, révocation de tokens. *(M)*
- [ ] **Journalisation de sécurité** centralisée et inviolable (connexions, échecs, verrouillages). *(M)*
- [ ] Politique de **rétention des logs** + horodatage fiable. *(S)*
- [ ] **Démarrer la collecte de preuves tôt** (période d'observation Type II). *(continu)*

**K. Tests (fiabilité)**

- [ ] Tests frontend prioritaires : **file offline terrain**, refresh+retry, gardes de rôle, interceptor JWT. *(M)*
- [ ] 🔗 Vrais **tests e2e Playwright** : ajouter les `data-testid`, parcours login / saisie index / paiement ; lancer en CI avec `webServer`. *(M)*
- [ ] Lancer **Vitest** (tests unitaires front) en CI. *(S)*
- [ ] Job CI backend sur **PostgreSQL** (pas seulement SQLite). *(S)*
- [ ] **Trivy** sur l'image frontend. *(S)*
- [ ] **Test de charge / performance** à la cible de trafic. *(M)*
- [ ] **Test de pénétration** avant go‑live. *(M)*

### 🟡 P2 — Important (industrialisation, qualité, montée en charge)

**L. Qualité du code & alignement fin**

- [ ] Activer **`strict` TypeScript** + `strictTemplates` (`tsconfig.json`). *(S)*
- [ ] 🔗 Introduire **graphql‑codegen** (types générés depuis le schéma de la gateway) — supprime la dérive silencieuse front/back. *(M)*
- [ ] **`mypy --strict`** (ou pyright) en CI backend. *(S)*
- [ ] 🔗 Uniformiser l'**index compteur** en `DecimalField(10,3)` + migration (`campagne/models.py:69`). *(S)*
- [ ] Extraire une **lib partagée** `sgfe_common.grpc` (intercepteur + factory de channel) — supprime 8 copies divergentes. *(M)*
- [ ] Découper les composants volumineux (`campagne-detail`, `abonne-detail`, `facture-detail`). *(M)*
- [ ] Accessibilité formulaires : `<label for>` explicites. *(S)*
- [ ] 🔗 Brancher `progressionUpdated` (temps réel campagne). *(S)*
- [ ] Ajouter `isSuperviseur` à `AuthService` (cohérence). *(S)*
- [ ] 🔗 Retirer le code vestigial : `extensions.grpc_code` jamais émis, resolver `dashboard` inutilisé, réactiver `typeEnvoi` (le champ existe). *(S)*
- [ ] Nettoyer les artefacts `.coverage` du dépôt. *(S)*

**M. Performance & montée en charge**

- [ ] **Pagination + recherche** sur les listes (abonnés, factures, paiements) — filtrage par statut uniquement aujourd'hui. *(M)*
- [ ] Index DB additionnels selon les requêtes réelles. *(S)*
- [ ] Cache (config, tarifs) si nécessaire. *(S)*

**N. Infra & résilience (SPOF)**

- [ ] Redondance **PostgreSQL** (réplication + failover). *(L)*
- [ ] **Redis** en réplication / Sentinel. *(M)*
- [ ] **WhatsApp** : file d'attente + rate‑limit + surveillance du risque de bannissement ; envisager l'**API officielle WhatsApp Business**. *(M)*
- [ ] **Dockerfile frontend durci** : digest SHA épinglé + `USER` non‑root. *(S)*
- [ ] **TLS partout** (le nginx backend est en HTTP nu) + renouvellement auto des certificats. *(M)*
- [ ] Environnement de **staging iso‑prod**. *(M)*
- [ ] **Chiffrement au repos** des PII abonné (nom, téléphone, adresse — `abonne/models.py:23`). *(M)*

### 🟢 P3 — Évolution (métier avancé, conformité, documentation)

**O. Métier avancé**

- [ ] **Tarification par tranches progressives** + redevance fixe + taxes (les régies d'eau facturent rarement au prix unique). *(L)*
- [ ] **Report d'arriérés** inter‑périodes (solde reporté sur la facture suivante). *(M)*
- [ ] **Pénalités de retard**. *(M)*
- [ ] **Estimation automatique** des compteurs non relevés (moyenne historique) + facturation sur estimation. *(M)*
- [ ] **Multi‑agence / multi‑tenant**. *(L)*
- [ ] **Retry automatique** des notifications en échec. *(S)*

**P. Conformité données & légal**

- [ ] **RGPD** : export structuré + effacement/anonymisation des données d'un abonné résilié. *(M)*
- [ ] Politique de **rétention des données**. *(S)*
- [ ] **CGU / politique de confidentialité** pour les abonnés. *(S)*

**Q. Documentation**

- [ ] 🔗 **Réaligner** `CLAUDE.md`/`docs/` sur la réalité (k8s absent, observabilité, PrimeNG, WeasyPrint, 4 rôles, couverture réelle) — ou livrer les éléments manquants. *(M)*
- [ ] **Runbook d'exploitation** (incidents, restauration, rollback). *(M)*
- [ ] Mettre à jour `docs/ETAT_DU_SYSTEME.md` (Reporting implémenté). *(S)*
- [ ] **Plan de reprise d'activité (DR)**. *(M)*
- [ ] i18n : externaliser les messages backend codés en dur (FR). *(M)*

### Portes de sortie (Go / No‑Go)

| Porte | Condition | Items requis |
|---|---|---|
| 🚦 **Go production (technique)** | Aucun secret exposé, périmètre gRPC verrouillé, système déployable + sauvegardé, bugs bloquants corrigés | **Tous les P0** + F, I (min. `/metrics` + healthchecks + logs) + K (e2e smoke vert) |
| 🔗 **Complètement aligné** | Front ⇄ back sans écart de contrat ni dérive | Tous les items **🔗** : D, E‑proxy, H, K‑e2e, L‑codegen/typeEnvoi/index, Q‑doc |
| 🛡️ **Prêt pour l'audit SOC 2 Type II** | Contrôles en place **et** preuves accumulées sur la période d'observation | P0 (sécurité) + J (audit trail) + I (monitoring) + K (pentest) + rate limiting (C) |
| 💰 **Complet fonctionnellement** | Cycle correctif financier + portail abonné opérationnels | G + H + M (pagination) |

### Décompte

| Priorité | Nombre d'items | Effort dominant |
|---|:---:|---|
| 🔴 P0 — Bloquant prod | 27 | S/M (+ 2 chantiers L : mTLS, déploiement) |
| 🟠 P1 — Critique | 32 | M (+ 2 L : outbox, avoir/rectification) |
| 🟡 P2 — Important | 21 | S/M (+ 1 L : réplication PostgreSQL) |
| 🟢 P3 — Évolution | 14 | M/L |
| **Total** | **94** | — |

> Ordre d'attaque conseillé : **A → D → E** (stopper l'exposition, rendre déployable, corriger les bugs bloquants) en semaine 1‑2, puis **B/C** (durcissement), puis **F/G/H/I/J** en continu. Les items 🔗 peuvent être traités en parallèle par un binôme front/back car ils sont peu coûteux et débloquent l'alignement complet rapidement.

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
