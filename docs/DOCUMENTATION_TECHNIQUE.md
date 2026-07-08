# Documentation technique — Système de Gestion de Facturation d'Eau (SGFE)

> Document exhaustif des fonctionnalités du backend. Source de vérité du contrat
> externe : le schéma GraphQL exposé par l'API Gateway. Chaque fonctionnalité
> est décrite avec : **but**, **implémentation**, **dépendances**, **plus-value**,
> **contrat GraphQL / REST**, **rôles**, **règles métier**, **temps réel**.

---

## 1. Introduction

### 1.1 Vue d'ensemble
Le SGFE est une plateforme de **facturation d'eau** organisée en **microservices**.
Un cycle métier type : un **abonné** possède un **compteur** ; une **campagne**
mensuelle de relevé collecte les **index** ; à la **clôture**, des **factures**
sont générées (consommation × prix du m³) puis **envoyées par WhatsApp** ; les
**paiements** sont enregistrés, les **impayés** relancés en 4 étapes ; un
**tableau de bord** agrège les statistiques.

### 1.2 Architecture (C4 — niveau conteneurs)
- **API Gateway** (`gateway/`) — unique point d'entrée du frontend. Expose un
  schéma **GraphQL** (Strawberry, ASGI) qui fédère des appels **gRPC** vers les
  microservices. **Aucune base de données.**
- **8 microservices** Django indépendants, chacun avec **sa propre base
  PostgreSQL**, communiquant **exclusivement** en gRPC (jamais d'accès croisé aux
  BD) :

| # | Service | Port gRPC | BD | Rôle |
|---|---|---|---|---|
| 1 | auth | 50051 | auth_db | JWT, utilisateurs, rôles |
| 2 | abonne | 50052 | abonne_db | Abonnés + compteurs + zones |
| 3 | campagne | 50053 | campagne_db | Campagnes + relevés + affectations |
| 4 | facturation | 50054 | facturation_db | Factures + PDF + tarifs |
| 5 | paiement | 50055 | paiement_db | Paiements + soldes + impayés |
| 6 | notification | 50056 | notification_db | WhatsApp + tokens espace abonné |
| 7 | reporting | 50057 | reporting_db | Tableau de bord (read model CQRS) |
| 8 | config | 50058 | config_db | Paramètres système + infos société |

- **whatsapp-service** (Node.js, `whatsapp-web.js`) — passerelle WhatsApp
  auto-hébergée (compte dédié, zéro coût), pilotée par le service notification.
- **Redis** — pub/sub (subscriptions GraphQL temps réel) **et** Streams
  (événementiel du read model reporting).
- **nginx** — sert le build Angular et proxifie `/graphql` sous le même domaine.

### 1.3 Stack technologique
Backend **Django 5.x** ; **gRPC + Protocol Buffers v3** (interne) ; **GraphQL
Strawberry** (gateway) ; **PostgreSQL 16** (1 instance/service) ; **ReportLab /
WeasyPrint** (PDF) ; **whatsapp-web.js** (WhatsApp) ; **Brevo API** (e-mails) ;
**JWT SimpleJWT** (access 24 h, refresh 7 j en cookie HttpOnly) ; **APScheduler**
(cron) ; **Redis** ; **Kubernetes/Minikube + Docker** ; **Angular 18 PWA** ;
**OpenTelemetry + Prometheus + Loki + Jaeger + Grafana** (observabilité).

### 1.4 Rôles & permissions
Enum GraphQL `Role` : `ADMIN`, `AGENT`, `COMPTABLE`, `SUPERVISEUR`.
`ADMIN` est super-utilisateur (accès total).

| Action | ADMIN | AGENT | COMPTABLE | SUPERVISEUR |
|---|:---:|:---:|:---:|:---:|
| Gérer les abonnés | ✅ | ❌ | ❌ | ❌ |
| Créer/clôturer une campagne | ✅ | ❌ | ❌ | ✅ (les siennes) |
| Saisir / corriger un index | ✅ | ✅ | ❌ | ✅ (ses campagnes) |
| Voir la progression / la tournée | ✅ | ✅ | ❌ | ✅ (ses campagnes) |
| Consulter/générer les factures | ✅ | ❌ | ✅ | ❌ |
| Enregistrer un paiement | ✅ | ❌ | ✅ | ❌ |
| Envoyer WhatsApp | ✅ | ❌ | ✅ | ❌ |
| Voir le dashboard | ✅ | ❌ | ✅ | ❌ |
| Gérer les utilisateurs | ✅ | ❌ | ❌ | ❌ |
| Modifier les paramètres | ✅ | ❌ | ❌ | ❌ |

Le filtrage `SUPERVISEUR` (ne voit que **ses** campagnes) est appliqué côté
Gateway (`_verifier_acces_campagne`) et relayé au service campagne via le
paramètre `created_by`.

### 1.5 Préoccupations transversales
- **Authentification** : chaque requête protégée passe par `require_auth`/
  `require_role` (context.py), qui valident le JWT en appelant `auth.ValidateToken`
  en gRPC (pas de décodage local — la source de vérité reste auth, qui peut
  révoquer un token). `UserPayload = {user_id, username, email, role, is_active,
  phone_number}`.
- **Codes d'erreur** (`extensions.code`) : `UNAUTHENTICATED`, `PERMISSION_DENIED`,
  `NOT_FOUND`, `INVALID_ARGUMENT`, `FAILED_PRECONDITION`, `ALREADY_EXISTS`,
  `SERVICE_UNAVAILABLE`. Le mapping exception→code est **centralisé par service**
  dans un `ErrorHandlingInterceptor` gRPC (les 8 services partagent le même
  pattern ; pas de try/except répété dans les servicers).
- **Architecture interne d'un service** : 3 couches — `grpc_server.py`
  (transport) / `services.py` (métier) / `repositories.py` (accès BD) —, plus
  `serializers.py` (modèle↔proto), `grpc_clients.py` (appels sortants),
  `event_publisher.py` (Redis).
- **Pas de FK inter-service** : les données d'un autre domaine sont **copiées en
  snapshot** (ex. `prix_m3` copié dans la facture ; `quartier/camp` copiés sur le
  relevé) — jamais de clé étrangère traversant un service.
- **Temps réel** : Redis **pub/sub** → subscriptions GraphQL (`abonne:events`,
  `facture:events`, `paiement:events`, `progression:events`, `tarif:events`,
  `config:events`, `whatsapp:events`, `user`).
- **Événementiel reporting** : Redis **Streams** (`reporting:stream`) — les
  producteurs publient (`XADD`), le read model consomme (consumer group, at-least-
  once, idempotent).
- **Observabilité** : logs JSON structurés avec `trace_id`, `/metrics` Prometheus,
  instrumentation OpenTelemetry (traces Jaeger).

### 1.6 Endpoints exposés par la Gateway
| Route | Type | Auth | Rôle |
|---|---|---|---|
| `POST /graphql` | GraphQL | JWT (par champ) | selon l'opération |
| `GET /factures/{id}/pdf/` | PDF | JWT | ADMIN, COMPTABLE |
| `GET /bilan-impayes/pdf/` | PDF | JWT | ADMIN, COMPTABLE |
| `GET /rapports/factures.csv` | CSV | JWT | ADMIN, COMPTABLE |
| `GET /rapports/paiements.csv` | CSV | JWT | ADMIN, COMPTABLE |
| `GET /rapports/synthese/pdf/` | PDF | JWT | ADMIN, COMPTABLE |
| `GET /espace-abonne/{token}/` | JSON | Token WhatsApp (public) | — |
| `GET /espace-abonne/{token}/facture/{id}/pdf/` | PDF | Token WhatsApp (public) | — |

### 1.7 Cron jobs
- **campagne — 7 h 00** (`campagne_planifiee_job`) : démarre les campagnes
  planifiées pour J ou J-1 (rattrapage).
- **paiement — 8 h 00** (`impaye_checker_job`) : parcourt les impayés et déclenche
  les relances par étape.

---

# Fonctionnalités

Chaque fonctionnalité suit le canevas : **But · Implémentation · Dépendances ·
Plus-value · Contrat · Rôles · Règles / Temps réel**.

---

## Domaine A — Authentification & Utilisateurs (`auth`, port 50051)

**Modèles** : `User` (custom `AUTH_USER_MODEL`, UUID, `role`, `failed_attempts`,
`locked_until`), `RevokedToken` (blacklist `jti`), `PasswordSetupToken`,
`PhoneOtpToken`. Ce service **n'appelle aucun autre service**.

### A.1 Connexion (login)
- **But** : authentifier un utilisateur par **username OU numéro de téléphone**
  (+237…) et lui délivrer un couple de tokens.
- **Implémentation** : `auth.Login` → génère un **access token** (24 h) et un
  **refresh token** (7 j). Côté Gateway, le refresh est posé en **cookie HttpOnly
  `SameSite=Strict`** (jamais exposé à JS) ; seul l'access token revient dans la
  réponse. **Verrouillage anti-brute-force** : après `MAX_LOGIN_ATTEMPTS` (défaut
  5) échecs, le compte est bloqué `LOCKOUT_DURATION_MINUTES` (défaut 15) — un
  login réussi remet le compteur à zéro.
- **Dépendances** : aucune (service racine).
- **Plus-value** : point d'entrée unique de sécurité ; protège contre le brute-force.
- **Contrat** : `mutation login(identifier: String!, password: String!): AuthPayload!`
  — `AuthPayload { accessToken, expiresIn, user { id username email phoneNumber role isActive createdAt } }`.
- **Erreurs** : `UNAUTHENTICATED` (« Identifiants invalides » ou « Compte
  verrouillé temporairement »).

### A.2 Rafraîchissement de token (refreshToken)
- **But** : obtenir un nouvel access token sans re-saisir les identifiants.
- **Implémentation** : `auth.RefreshToken` lit le **cookie HttpOnly** `refresh_token`
  (le frontend n'envoie rien dans le corps), vérifie qu'il n'est pas révoqué,
  émet un nouveau couple et **repose le cookie** (rotation).
- **Dépendances** : login (émission initiale du cookie).
- **Plus-value** : sessions longues et sûres (le refresh ne transite jamais par JS).
- **Contrat** : `mutation refreshToken: AuthPayload!`.

### A.3 Déconnexion (logout)
- **But** : invalider le token courant.
- **Implémentation** : `auth.Logout` ajoute le `jti` du token à `RevokedToken`
  (blacklist vérifiée à chaque `ValidateToken`/`RefreshToken`) et efface le cookie.
- **Contrat** : `mutation logout: Boolean!`.

### A.4 Profil courant (me)
- **But** : récupérer le profil (dont le **rôle**) de l'utilisateur connecté.
- **Implémentation** : `require_auth` → `auth.GetUser(user_id)`.
- **Contrat** : `query me: User`.

### A.5 Gestion des utilisateurs
- **But** : CRUD des comptes (back-office).
- **Implémentation** : `auth.CreateUser/UpdateUser/DeactivateUser/ReactivateUser/
  ListUsers`. À la création : **ADMIN** → e-mail + téléphone requis, **activation
  par e-mail** (Brevo) ; **autres rôles** → téléphone requis, **activation par OTP
  WhatsApp**. Émet `USER_CREATED`/`USER_UPDATED` (le changement de rôle est notifié).
- **Dépendances** : notification (OTP WhatsApp), Brevo (e-mail).
- **Plus-value** : onboarding sécurisé sans mot de passe temporaire.
- **Contrat** :
  - `query users: [User!]!` — **ADMIN uniquement**.
  - `mutation createUser(username, phoneNumber, role: Role!, email=""): User!`
  - `mutation updateUser(id: ID!, email="", role, phoneNumber=""): User!`
  - `mutation deactivateUser(id: ID!): User!` · `mutation reactivateUser(id: ID!): User!`
- **Temps réel** : `subscription utilisateurUpdated(utilisateurId): User!`.

### A.6 Agents disponibles (agentsDisponibles)
- **But** : lister les **AGENT actifs** pour peupler un sélecteur d'affectation.
- **Implémentation** : Gateway appelle `auth.ListUsers` et filtre `role=AGENT` +
  `is_active` (côté Gateway).
- **Dépendances** : campagne (affectation).
- **Plus-value** : débloque l'affectation d'agents pour le **SUPERVISEUR** sans lui
  ouvrir `users` (réservé ADMIN).
- **Contrat** : `query agentsDisponibles: [User!]!` — **ADMIN + SUPERVISEUR**.

### A.7 Activation de compte & OTP téléphone
- **But** : première définition du mot de passe.
- **Implémentation** : `activateAccount(token, password)` (lien e-mail) ;
  `requestPhoneOtp(phoneNumber)` → code 6 chiffres par WhatsApp puis
  `verifyOtpAndSetPassword(phoneNumber, otpCode, password)`. Réponses
  **non révélatrices** (ne disent jamais si le numéro/compte existe).
- **Dépendances** : notification (WhatsApp), Brevo.
- **Contrat** : `mutation activateAccount(token, password): Boolean!` ·
  `mutation requestPhoneOtp(phoneNumber): OtpSentPayload!` (`{ maskedPhone }`) ·
  `mutation verifyOtpAndSetPassword(phoneNumber, otpCode, password): Boolean!`.

### A.8 Réinitialisation de mot de passe
- **But** : récupérer l'accès à un compte.
- **Implémentation** : `requestPasswordReset(email)` (self-service e-mail) +
  `resetPassword(token, password)` ; `resetUserPassword(id)` (ADMIN) renvoie les
  identifiants — sert à la fois de « renvoyer le lien d'activation » et de
  « réinitialiser » selon l'état du compte, via le canal adéquat (e-mail ou OTP).
- **Contrat** : `mutation requestPasswordReset(email): Boolean!` ·
  `mutation resetPassword(token, password): Boolean!` ·
  `mutation resetUserPassword(id: ID!): User!` (**ADMIN**).

---

## Domaine B — Abonnés & Compteurs (`abonne`, port 50052)

**Modèles** : `Abonne` (`numero_abonne` AB-XXXX auto, `statut` ACTIF/SUSPENDU/
RESILIE), `Compteur` (`numero_compteur`, `quartier`, `camp`, `index_initial`,
`statut` ACTIF/REMPLACE/DESACTIVE — un seul ACTIF par abonné), `HistoriqueCompteur`.

### B.1 Création d'un abonné (createAbonne)
- **But** : enregistrer un abonné **et son compteur** en une opération.
- **Implémentation** : `abonne.CreateAbonne` — `numero_abonne` généré (AB-XXXX
  séquentiel), compteur **obligatoire** créé dans la foulée. Émet `ABONNE_CREATED`.
- **Plus-value** : garantit qu'un abonné a toujours un compteur relevable.
- **Contrat** : `mutation createAbonne(input: CreateAbonneInput!): Abonne!`
  (`CreateAbonneInput { nom, prenom, telephoneWhatsapp, adresse, numeroCompteur,
  quartier, camp, indexInitial, datePose }`).
- **Rôles** : ADMIN.

### B.2 Consultation des abonnés
- **But** : lister/consulter les abonnés.
- **Contrat** : `query abonne(id: ID!): Abonne` · `query abonnes(statut: StatutAbonne): [Abonne!]!`
  · `query abonnesActifs: [Abonne!]!` (consommé par campagne pour ne relever que
  les actifs). Type `Abonne { id numeroAbonne nom prenom telephoneWhatsapp adresse
  statut compteur { … } createdAt }`.

### B.3 Mise à jour d'un abonné (updateAbonne)
- **Contrat** : `mutation updateAbonne(id: ID!, input: UpdateAbonneInput!): Abonne!`
  (champs partiels nom/prénom/téléphone/adresse). Émet `ABONNE_UPDATED`. **ADMIN**.

### B.4 Cycle de vie de l'abonné
- **But** : suspendre / réactiver / résilier.
- **Implémentation** : `SuspendreAbonne` / `ReactiverAbonne` / `ResilierAbonne`.
  Un abonné **suspendu ou résilié** disparaît de `ListAbonnesActifs` et **ne peut
  pas être relevé** (règle vérifiée par campagne à la saisie).
- **Dépendances** : consommé par campagne (contrôle ACTIF) ; la suspension est
  aussi déclenchée par paiement (étape 4 des impayés).
- **Contrat** : `mutation suspendreAbonne(id): Abonne!` · `reactiverAbonne(id): Abonne!`
  · `resilierAbonne(id): Abonne!`. **ADMIN**.
- **Temps réel** : `subscription abonneUpdated(abonneId): Abonne!`.

### B.5 Gestion du compteur
- **But** : mettre à jour ou **remplacer** le compteur (garde l'historique).
- **Implémentation** : `UpdateCompteur` (quartier/camp/index/date) ;
  `RemplacerCompteur` archive l'ancien (`statut=REMPLACE`), crée le nouveau
  (`ACTIF`), trace dans `HistoriqueCompteur` ; valide `index_fermeture >=
  index_initial` de l'ancien.
- **Plus-value** : traçabilité complète du parc compteurs.
- **Contrat** : `mutation updateCompteur(abonneId, input: UpdateCompteurInput!): Compteur!`
  · `mutation remplacerCompteur(abonneId, input: RemplacerCompteurInput!): Compteur!`
  · `query historiqueCompteur(id: ID!): [HistoriqueCompteur!]!`. **ADMIN**.

### B.6 Zones de relevé (ListZones / zonesDisponibles)
- **But** : exposer les **zones** (paire quartier + camp) et le nombre d'abonnés
  actifs par zone.
- **Implémentation** : `abonne.ListZones` agrège les compteurs ACTIF d'abonnés
  ACTIF par (quartier, camp).
- **Dépendances** : campagne (affectation par zone), Gateway (dénominateurs de
  l'écran « détail campagne »).
- **Contrat** : `query zonesDisponibles: [ZoneDisponible!]!`
  (`{ quartier camp nbAbonnes }`). **ADMIN + SUPERVISEUR**.

---

## Domaine C — Campagnes & Relevés (`campagne`, port 50053)

**Modèles** : `Campagne` (cycle PLANIFIEE→EN_COURS→CLOTUREE, `created_by`,
toggles `generer_factures_auto`/`envoyer_whatsapp_auto`), `Releve` (unique par
(campagne, abonné), `statut` A_RELEVER/RELEVE/NON_RELEVE/ESTIME, `agent_id`,
snapshot `quartier`/`camp`), `CampagneAgent` (affectation globale), `AffectationZone`
(agent↔zone, une zone = un agent), `ReleveAudit` (journal SAISIE/CORRECTION).

### C.1 Création d'une campagne (creerCampagne)
- **But** : ouvrir une campagne de relevé mensuelle.
- **Implémentation** : `campagne.CreateCampagne` — statut initial PLANIFIEE (ou
  EN_COURS si `demarrerMaintenant`). `created_by` = utilisateur courant (base du
  filtrage SUPERVISEUR). Numéro Mobile Money optionnel (9 chiffres) imprimé sur
  les factures.
- **Plus-value** : cadre temporel unique par période, base de la traçabilité.
- **Contrat** : `mutation creerCampagne(input: CreateCampagneInput!): Campagne!`
  (`{ nom periodeMois periodeAnnee datePlanifiee numeroMobileMoney
  genererFacturesAuto envoyerWhatsappAuto demarrerMaintenant }`).
- **Rôles** : ADMIN, SUPERVISEUR (en devient propriétaire).

### C.2 Consultation des campagnes
- **Implémentation** : filtrage par rôle — ADMIN : toutes ; SUPERVISEUR : ses
  campagnes (`created_by`) ; AGENT : celles où il est affecté.
- **Contrat** : `query campagne(campagneId): Campagne!` · `query campagnes: [Campagne!]!`
  (auto-filtré). Type `Campagne { campagneId nom periodeMois periodeAnnee statut
  datePlanifiee dateCreation dateCloture numeroMobileMoney genererFacturesAuto
  envoyerWhatsappAuto }` (statut : PLANIFIEE | EN_COURS | CLOTUREE).

### C.3 Affectation globale d'un agent (affecterAgent)
- **But** : donner à un agent l'accès à une campagne entière.
- **Implémentation** : `campagne.AssignerAgent` (idempotent) → `CampagneAgent`.
- **Dépendances** : auth (`agentsDisponibles` pour le sélecteur).
- **Contrat** : `mutation affecterAgent(campagneId, agentId): Campagne!`.
- **Rôles** : ADMIN, SUPERVISEUR (les siennes).

### C.4 Affectation par zone (affecterZones)
- **But** : rattacher un agent à des **zones** précises (quartier + camp) — écran
  « tournée agent ».
- **Implémentation** : `campagne.AffecterZones` fixe l'ensemble exact des zones de
  l'agent (réaffecte une zone détenue par un autre — une zone = un seul agent) et
  garantit son affectation globale (pour qu'il puisse saisir). **Coexiste** avec
  l'affectation globale.
- **Dépendances** : abonne (`ListZones`), auth.
- **Plus-value** : pilotage fin des tournées, avancement par zone.
- **Contrat** : `mutation affecterZones(campagneId, agentId, zones: [ZoneInput!]!): [AgentAffecte!]!`.
- **Rôles** : ADMIN, SUPERVISEUR (les siennes).

### C.5 Agents affectés & répartition par zone (écran « détail campagne »)
- **But** : cartes des agents (statut de tournée, avancement) + tableau par zone.
- **Implémentation** : `campagne.ListAgentsCampagne` renvoie, par agent : ses zones
  (avec nb de relevés de la zone), son total de relevés saisis, la **date de son
  dernier relevé**. La Gateway **enrichit** : nom/rôle via `auth.ListUsers` (**1
  seul appel** indexé — pas de N+1), nb d'abonnés par zone via `abonne.ListZones`,
  et **dérive le statut de tournée** depuis la dernière activité (`EN_TOURNEE`
  ≤15 min, `ACTIF` ≤2 h, `EN_RETARD` au-delà, `INACTIF` si aucun relevé).
- **Dépendances** : abonne (`ListZones`), auth (`ListUsers`).
- **Plus-value** : supervision temps réel des tournées sans heartbeat dédié.
- **Contrat** :
  - `query agentsCampagne(campagneId): [AgentAffecte!]!` — `AgentAffecte { agentId
    username role statut derniereActivite nbReleves zones { quartier camp nbAbonnes
    nbReleves pct } }`.
  - `query repartitionParZone(campagneId): [ZoneRepartition!]!` — une ligne par
    zone (`{ quartier camp agentId agentUsername nbAbonnes nbReleves pct }`).
- **Rôles** : ADMIN, AGENT, SUPERVISEUR (les siennes).
- **Temps réel** : rafraîchies via `progressionUpdated` (l'événement porte l'agent).

### C.6 Saisie d'un index (saisirIndex)
- **But** : enregistrer le nouvel index relevé d'un abonné.
- **Implémentation** : `campagne.SaisirIndex` — crée le relevé à la volée si absent
  (après **contrôle du statut ACTIF** de l'abonné via `abonne.GetAbonne`), copie la
  **zone** (quartier/camp) du compteur en snapshot, calcule la consommation, passe
  le relevé en RELEVE, et écrit une entrée d'audit **SAISIE** (auteur en snapshot).
  Publie `progressionUpdated` (avec `agent_id`).
- **Dépendances** : abonne (statut + zone).
- **Règles** : campagne EN_COURS ; `nouveau_index >= ancien_index` ; abonné ACTIF.
- **Contrat** : `mutation saisirIndex(input: SaisirIndexInput!): Releve!`
  (`{ campagneId abonneId nouveauIndex observation }`).
- **Rôles** : ADMIN, AGENT, SUPERVISEUR (les siennes).

### C.7 Correction d'un index (corrigerReleve) + journal d'audit
- **But** : rectifier un index **déjà relevé**, y compris après clôture.
- **Implémentation** : `campagne.CorrigerReleve` — préserve l'agent d'origine et la
  date de relevé, recalcule la consommation, ajoute une entrée d'audit
  **CORRECTION**. Autorisée quel que soit le statut de la campagne.
- **Plus-value** : correction d'erreur traçable sans perdre l'historique.
- **Contrat** : `mutation corrigerReleve(input: CorrigerReleveInput!): Releve!`.
  Le type `Releve` expose `saisiPar { id username role }`, `saisiLe` et le journal
  `audit { action auteur ancienIndex nouvelIndex horodatage }`.
- **Rôles** : ADMIN, SUPERVISEUR (les siennes).
- **Note** : la **propagation financière** d'une correction post-clôture
  (régénération facture / recalcul solde) n'est pas encore implémentée.

### C.8 Marquer non relevé / estimé (marquerNonReleve)
- **But** : gérer un compteur inaccessible ou illisible.
- **Implémentation** : `campagne.MarquerNonReleve` → statut NON_RELEVE ou ESTIME.
- **Contrat** : `mutation marquerNonReleve(input: MarquerNonReleveInput!): Releve!`
  (`statut` = NON_RELEVE | ESTIME).

### C.9 Consultation des relevés
- **Contrat** : `query releves(campagneId): [Releve!]!` ·
  `query relevesParAgent(campagneId, agentId): [Releve!]!` (filtrage côté Gateway ;
  un AGENT ne consulte que **sa** propre tournée) ·
  `query dernierIndex(abonneId): DernierIndex!` (pré-remplissage saisie).

### C.10 Progression & résumé de clôture
- **But** : suivre l'avancement, préparer la clôture.
- **Contrat** : `query progression(campagneId): Progression!`
  (`{ totalAbonnes nbReleves nbEnAttente pourcentage }`) ·
  `query resumeCloture(campagneId): ResumeCloture!` (ventilation fine
  `{ nbReleves nbEstimes nbNonReleves nbRestants nbFacturesAGenerer }`, **ADMIN +
  SUPERVISEUR**).
- **Temps réel** : `subscription progressionUpdated(campagneId): Progression!`.

### C.11 Clôture de campagne (cloturerCampagne)
- **But** : figer la campagne et déclencher la facturation.
- **Implémentation** : `campagne.CloturerCampagne` (EN_COURS→CLOTUREE) → si
  `generer_factures_auto`, **notifie facturation** (CampagneCloturee) ; publie un
  événement **CAMPAGNE_STATS** sur le flux reporting.
- **Dépendances** : facturation (génération), reporting (stats).
- **Contrat** : `mutation cloturerCampagne(campagneId): Campagne!`. **ADMIN,
  SUPERVISEUR (les siennes)**.

### C.12 Démarrage automatique (cron 7 h)
- **But** : passer en EN_COURS les campagnes planifiées pour J/J-1.
- **Implémentation** : `campagne_planifiee_job` (APScheduler, 7 h 00) — démarre
  **toutes** les campagnes planifiées de la date (rattrapage J-1).

---

## Domaine D — Facturation & Tarifs (`facturation`, port 50054)

**Modèles** : `Tarif` (un seul actif, `prix_m3`), `Facture` (numéro
`FACT-AAAA-MM-XXXX`, `prix_m3` **copié**, `statut` IMPAYEE/PARTIELLE/PAYEE).
Appelle abonne, campagne, config. Le read reporting est appelé pour la synthèse.

### D.1 Tarif du m³ (tarifActuel / updateTarif)
- **But** : gérer le prix du m³ appliqué aux nouvelles factures.
- **Implémentation** : `GetTarifActuel` / `UpdateTarif` désactive l'ancien tarif et
  en crée un nouveau. Le `prix_m3` est **copié** dans chaque facture (jamais de FK)
  → une modification de tarif n'affecte pas les factures existantes.
- **Contrat** : `query tarifActuel: Tarif!` (`{ tarifId prixM3 dateEffet isActive }`)
  · `mutation updateTarif(prixM3: Float!, dateEffet: String!): Tarif!`. **ADMIN**.
- **Temps réel** : `subscription tarifUpdated: Tarif!`.

### D.2 Génération des factures (genererFactures)
- **But** : produire une facture par relevé à la clôture.
- **Implémentation** : `facturation.GenererFactures` appelle `campagne.ListReleves`,
  ne garde que les **RELEVE** (ignore NON_RELEVE/ESTIME), revalide
  `nouveau_index >= ancien_index` (défense en profondeur), calcule
  `montant = consommation × prix_m3`, numérote (`FACT-AAAA-MM-XXXX`, séquence
  verrouillée par `select_for_update`), génère le PDF, et **publie un événement
  FACTURATION_STATS (GENEREE)**. Notifie paiement (création du solde) et publie
  `factureUpdated`. `date_limite_paiement = date_releve + delai_paiement_jours`
  (config, défaut 5).
- **Dépendances** : campagne (relevés), config (délai, infos société), paiement
  (solde), notification (envoi), reporting (stats).
- **Erreurs** : `FAILED_PRECONDITION` si aucun tarif actif ; `UNAVAILABLE` si
  campagne injoignable.
- **Contrat** : `mutation genererFactures(campagneId, envoyerWhatsappAuto=true): [Facture!]!`.
  **ADMIN, COMPTABLE**.

### D.3 Consultation des factures
- **Contrat** : `query facture(factureId): Facture!` ·
  `query factures(campagneId="", abonneId="", statut=""): [Facture!]!` ·
  `query facturesParCampagne(campagneId): [Facture!]!`. Type `Facture { factureId
  numeroFacture abonneId campagneId ancienIndex nouveauIndex consommation prixM3
  montant statut dateReleve dateLimitePaiement dateGeneration pdfPath
  numeroMobileMoney }`. **ADMIN, COMPTABLE**.

### D.4 PDF de facture
- **But** : produire le PDF (gabarit « AquaBill », WeasyPrint, police Montserrat).
- **Implémentation** : `GetFacturePDF` — rendu du gabarit `facture_pdf.html`,
  généré à la création et stocké (`PDF_STORAGE_DIR`), régénéré à la volée si absent.
  Inclut l'historique de consommation des 6 derniers mois et le lien vers l'espace
  abonné (masqué tant qu'aucun token n'existe).
- **Contrat (REST)** : `GET /factures/{factureId}/pdf/` (JWT, **ADMIN/COMPTABLE**) ;
  et via l'espace abonné public (voir F.7).

### D.5 Bilan des impayés (PDF)
- **But** : document A4 agrégé des factures non soldées (back-office).
- **Implémentation** : `GenererBilanImpayesPDF` agrège les impayés (via paiement),
  calcule le retard et l'étape de relance par abonné, produit synthèse +
  répartition par étape + tableau détaillé.
- **Contrat (REST)** : `GET /bilan-impayes/pdf/` (JWT, **ADMIN/COMPTABLE**).

### D.6 Synthèse de campagne & exports (écran 13)
- **But** : rapports back-office par campagne.
- **Implémentation** : `GenererSyntheseCampagnePDF` (stats 3 domaines, lit le read
  model reporting) ; exports CSV factures/paiements.
- **Contrat (REST)** : `GET /rapports/synthese/pdf/` · `GET /rapports/factures.csv`
  · `GET /rapports/paiements.csv` (JWT, **ADMIN/COMPTABLE**).

### D.7 Mise à jour du statut de facture (updateStatutFacture)
- **But** : refléter le passage IMPAYEE→PARTIELLE→PAYEE.
- **Implémentation** : `UpdateStatutFacture` — appelé par paiement après chaque
  versement ; publie `factureUpdated` et, au passage **PAYEE**, un événement
  **FACTURATION_STATS (PAYEE)**.
- **Contrat** : `mutation updateStatutFacture(factureId, statut): Facture!`.
- **Temps réel** : `subscription factureUpdated(campagneId): Facture!`.

---

## Domaine E — Paiements & Impayés (`paiement`, port 50055)

**Modèles** : `Paiement` (versement, `mode_paiement`), `SoldeFacture` (PK =
facture_id, `statut` IMPAYEE/PARTIELLE/PAYEE, `campagne_id`), `SuiviImpaye`
(4 étapes de relance). Appelle facturation, notification, abonne, config.

### E.1 Initialisation du solde (interne)
- **But** : ouvrir le solde d'une facture nouvellement générée.
- **Implémentation** : `paiement.InitialiserSolde` — appelé par facturation à la
  génération. `montant_total` = montant facture, statut IMPAYEE.
- **Dépendances** : facturation (émetteur).

### E.2 Enregistrement d'un paiement (enregistrerPaiement)
- **But** : enregistrer un versement (partiel ou total) et mettre à jour le solde.
- **Implémentation** : `paiement.EnregistrerPaiement` (transaction atomique +
  `select_for_update` sur le solde) — recalcule
  `solde_restant = montant_total − Σ versements`, en déduit le statut, **synchronise
  facturation** (`UpdateStatutFacture`, dégradation gracieuse), résout/suspend les
  relances, publie **PAIEMENT_STATS** (PAIEMENT, et IMPAYE_RESOLU si soldée) sur le
  flux reporting, et publie `paiementCree`.
- **Dépendances** : facturation (statut), notification (relances), abonne
  (suspension), reporting (stats).
- **Règles** : `montant > 0` ; `montant <= solde_restant` (pas de surpaiement) ;
  `reference_transaction` **obligatoire** pour MOBILE_MONEY et VIREMENT ; statut =
  IMPAYEE (0), PARTIELLE (0<payé<total), PAYEE (payé≥total).
- **Contrat** : `mutation enregistrerPaiement(factureId, abonneId, montant: Float!,
  datePaiement, modePaiement, referenceTransaction=""): Paiement!`
  (`Paiement { paiementId factureId montant datePaiement modePaiement
  referenceTransaction createdAt operateur statutFacture }`). **ADMIN, COMPTABLE**.
- **Temps réel** : `subscription paiementCree(campagneId): Paiement!`.

### E.3 Solde d'une facture (soldeFacture)
- **Contrat** : `query soldeFacture(factureId): SoldeFacture!`
  (`{ factureId montantTotal montantPaye soldeRestant statut }`).

### E.4 Liste des paiements
- **Contrat** : `query paiements(factureId="", abonneId=""): [Paiement!]!` ;
  `ListPaiementsParCampagne` alimente l'export CSV (écran 13).

### E.5 Impayés & suivi de relance
- **But** : lister les factures en retard et suivre l'escalade.
- **Implémentation** : `ListImpayes` (date limite dépassée, non payées) ;
  `GetSuiviImpaye` (étape courante 1→4).
- **Contrat** : `query impayes: [SoldeFacture!]!` ·
  `query suiviImpaye(factureId): SuiviImpaye!` (`{ suiviId factureId abonneId
  dateDepassement etapeActuelle resoluLe }`). **ADMIN, COMPTABLE**.

### E.6 Relances automatiques (cron 8 h)
- **But** : relancer les impayés par escalade.
- **Implémentation** : `impaye_checker_job` (APScheduler, 8 h 00) — pour chaque
  `SoldeFacture` en retard non payé :
  - **Étape 1 (J+0)** : rappel WhatsApp · **Étape 2 (J+3)** : 2ᵉ rappel ·
    **Étape 3 (J+7)** : avertissement · **Étape 4 (J+10)** : **suspension de
    l'abonné** + notification.
  Délais configurables (config). Après passage PARTIELLE, les relances sont
  suspendues N jours (défaut 5).
- **Dépendances** : notification (WhatsApp étapes 1-4), abonne (suspension étape 4).
- **Plus-value** : recouvrement automatisé et gradué.

---

## Domaine F — Notifications & Espace abonné (`notification`, port 50056)

**Modèles** : `Envoi` (`type_envoi`, `statut`), `TokenAcces` (accès public tokenisé
à l'espace abonné). Passerelle **whatsapp-web.js** (Node.js) + **Brevo** (e-mail).

### F.1 Envoi d'une facture par WhatsApp (envoyerFactureWhatsapp)
- **But** : transmettre le lien/PDF de facture à l'abonné.
- **Implémentation** : `notification.EnvoyerFacture` — crée un `Envoi`, génère un
  **token d'accès** à l'espace abonné, envoie via whatsapp-service. Trace le statut
  (ENVOYE/ECHEC).
- **Dépendances** : facturation (facture), abonne (téléphone), whatsapp-service.
- **Contrat** : `mutation envoyerFactureWhatsapp(factureId, abonneId): Envoi!`.
  **ADMIN, COMPTABLE**.

### F.2 Renvoi (renvoyerFactureWhatsapp / renvoyerEnvoi)
- **But** : renvoyer une facture / rejouer un envoi échoué (écran 23).
- **Contrat** : `mutation renvoyerFactureWhatsapp(factureId): Envoi!` ·
  `mutation renvoyerEnvoi(envoiId): Envoi!`.

### F.3 Envoi en masse (envoyerToutesFacturesWhatsapp)
- **But** : envoyer toutes les factures d'une campagne.
- **Contrat** : `mutation envoyerToutesFacturesWhatsapp(campagneId): Int!` (nombre
  envoyé). **ADMIN, COMPTABLE**.

### F.4 Consultation des envois
- **Contrat** : `query envoi(envoiId): Envoi!` ·
  `query envois(factureId="", abonneId=""): [Envoi!]!` (`Envoi { envoiId abonneId
  factureId typeEnvoi statut dateEnvoi messageId raisonEchec … }`).

### F.5 État de la passerelle WhatsApp (whatsappQr)
- **But** : coupler le compte WhatsApp (scan du QR) et suivre l'état de connexion.
- **Implémentation** : `GetWhatsAppQr` — remonte l'état de whatsapp-service (QR,
  prêt, numéro). Poussé en temps réel.
- **Contrat** : `query whatsappQr: WhatsAppQr!` (`{ ready qr number }`).
- **Temps réel** : `subscription whatsappStatus: WhatsAppQr!`.

### F.6 Test d'envoi (testerEnvoiWhatsapp)
- **Contrat** : `mutation testerEnvoiWhatsapp(phoneNumber): TestEnvoiResult!`
  (`{ success message }`). **ADMIN**.

### F.7 Espace abonné public (tokenisé — EF-NOTIF-003)
- **But** : permettre à l'abonné de **consulter ses factures et son PDF sans
  authentification**, via le lien reçu par WhatsApp.
- **Implémentation** : la Gateway valide le token (`notification.ValiderToken`),
  récupère les factures (facturation) et enrichit avec les soldes (paiement).
  Contrôle **anti-IDOR** sur le PDF : la facture doit appartenir à l'abonné du token
  (sinon 404). Best-effort sur le solde.
- **Dépendances** : notification (token), facturation (factures/PDF), paiement
  (soldes).
- **Plus-value** : self-service abonné, zéro compte à gérer.
- **Contrat (REST, public)** :
  - `GET /espace-abonne/{token}/` → JSON `{ abonne_id, token_expiration,
    factures: [{ facture_id, numero, date_releve, montant, statut,
    date_limite_paiement, solde_restant, montant_paye }] }`.
  - `GET /espace-abonne/{token}/facture/{factureId}/pdf/` → flux PDF.
  - Erreurs : **401** token invalide/expiré, **404** facture d'un autre abonné,
    **503** service indisponible.
- **Durée de vie** : `token_validite_jours` (config, défaut 20).

### F.8 Révocation des tokens abonné
- **Contrat** : `mutation revoquerTokenAbonne(tokenId): Boolean!` ·
  `mutation revoquerTousTokensAbonnes: Int!`. **ADMIN**.

---

## Domaine G — Configuration (`config`, port 50058)

**Modèles** : `InfosSociete` (identité imprimée sur les PDF), `ConfigParam`
(paramètres système clé/valeur : délais de paiement, validité des tokens, délais
de relance, pause des relances…).

### G.1 Infos société
- **But** : identité de l'émetteur (nom, adresse, téléphone, logo) sur les PDF.
- **Contrat** : `query infosSociete: InfosSociete!` ·
  `mutation updateInfosSociete(input: UpdateInfosSocieteInput!): InfosSociete!`.
  **ADMIN**.

### G.2 Paramètres système
- **But** : régler le comportement métier sans redéploiement (délai de paiement,
  validité token, étapes de relance, pause des relances…).
- **Implémentation** : `GetConfig` / `ListConfigs` / `UpdateConfig`. Lu par
  facturation (délai limite), paiement (relances), notification (validité token).
- **Contrat** : `query config(cle): ConfigParam!` · `query configs: [ConfigParam!]!`
  · `mutation updateConfig(cle, valeur): ConfigParam!`
  (`ConfigParam { cle valeur description }`). **ADMIN**.
- **Temps réel** : `subscription configUpdated(cle): ConfigParam!`.

---

## Domaine H — Reporting / Tableau de bord (`reporting`, port 50057) — CQRS

**Modèles** (read model dénormalisé, 1 ligne/campagne) : `StatsCampagne`,
`StatsFacturation`, `StatsPaiements`, `ProcessedEvent` (idempotence). Ce service
**n'a pas de logique métier propre** : c'est le **côté Query d'un CQRS** (ADR-019),
alimenté par événements.

### H.1 Tableau de bord & statistiques
- **But** : agréger l'état courant (campagne en cours, facturation, paiements) et
  les historiques.
- **Contrat** :
  - `query dashboard: Dashboard!` (`{ campagneEnCours facturationEnCours
    paiementsEnCours }`).
  - `query statsCampagne(campagneId): StatsCampagne!` (`{ campagneId nomCampagne
    totalAbonnes nbReleves nbEnAttente pourcentageProgression consommationTotale }`).
  - `query statsGlobales: StatsGlobales!` (`{ historiqueCampagnes[]
    consommationTotaleGlobale montantTotalFactureGlobal montantTotalEncaisseGlobal }`).
- **Rôles** : ADMIN, COMPTABLE.
- **Erreur** : `SERVICE_UNAVAILABLE` si le reporting n'est pas joignable (le
  frontend doit dégrader ce widget sans bloquer le reste).

### H.2 Alimentation événementielle (Redis Streams)
- **But** : maintenir le read model **de façon découplée et durable**.
- **Implémentation** : les producteurs publient des événements (`XADD` sur
  `reporting:stream`) au lieu d'un appel gRPC synchrone :
  - campagne (clôture) → **CAMPAGNE_STATS** (valeurs absolues, idempotent) ;
  - facturation (génération/paiement) → **FACTURATION_STATS** (incrément) ;
  - paiement (versement/résolution) → **PAIEMENT_STATS** (incrément).
  Un **consumer group** (`XREADGROUP`/`XACK`) tourne dans un **thread daemon** du
  serveur gRPC reporting ; livraison **at-least-once** ; **idempotence** via
  `ProcessedEvent` (dédup `event_id`, même transaction que la mise à jour). Rattrape
  les entrées non acquittées au redémarrage. Les RPC `UpdateStats*` restent exposés
  pour backfill/correction.
- **Plus-value** : reporting momentanément down ⇒ **aucune perte** de stats
  (rattrapage) ; producteurs jamais bloqués.

---

## Annexes transversales

### X.1 Subscriptions GraphQL (temps réel, Redis pub/sub)
| Subscription | Déclencheur | Canal Redis |
|---|---|---|
| `abonneUpdated(abonneId)` | création/màj/statut abonné | `abonne:events` |
| `factureUpdated(campagneId)` | génération / changement de statut facture | `facture:events` |
| `paiementCree(campagneId)` | enregistrement d'un paiement | `paiement:events` |
| `progressionUpdated(campagneId)` | saisie d'index (porte `agentId`) | `progression:events` |
| `tarifUpdated` | mise à jour du tarif | `tarif:events` |
| `configUpdated(cle)` | mise à jour d'un paramètre | `config:events` |
| `utilisateurUpdated(utilisateurId)` | création/màj/changement de rôle | `user` |
| `whatsappStatus` | changement d'état de la passerelle WhatsApp | `whatsapp:events` |

Les subscriptions WebSocket s'authentifient via `connectionParams` (JWT).

### X.2 Événements du read model reporting (Redis Streams `reporting:stream`)
| Type | Producteur | Sémantique |
|---|---|---|
| `CAMPAGNE_STATS` | campagne (clôture) | SET (valeurs absolues) |
| `FACTURATION_STATS` | facturation (GENEREE / PAYEE) | INCREMENT (delta) |
| `PAIEMENT_STATS` | paiement (PAIEMENT / IMPAYE_RESOLU) | INCREMENT (delta) |

Chaque événement porte un `event_id` (UUID) pour la déduplication.

### X.3 Codes d'erreur GraphQL (`extensions.code`)
`UNAUTHENTICATED` · `PERMISSION_DENIED` · `NOT_FOUND` · `INVALID_ARGUMENT` ·
`FAILED_PRECONDITION` · `ALREADY_EXISTS` · `SERVICE_UNAVAILABLE`.
Gérer côté client via `error.graphQLErrors[].extensions.code` ; utiliser
`errorPolicy: 'all'` pour rendre les résultats partiels quand un service est down.

### X.4 Jobs planifiés (APScheduler)
| Job | Service | Heure | Rôle |
|---|---|---|---|
| `campagne_planifiee_job` | campagne | 07 h 00 | démarre les campagnes planifiées (J/J-1) |
| `impaye_checker_job` | paiement | 08 h 00 | relances impayés (4 étapes) |

### X.5 Règles métier centrales
```python
consommation = nouveau_index - ancien_index           # >= 0
montant      = consommation * prix_m3                  # prix_m3 copié du tarif actif
solde_restant = montant_total - sum(versements)
date_limite   = date_releve + delai_paiement_jours     # config, défaut 5
statut_facture: IMPAYEE (payé=0) | PARTIELLE (0<payé<total) | PAYEE (payé>=total)
# MOBILE_MONEY / VIREMENT -> reference_transaction obligatoire
# saisie -> abonné ACTIF requis ; nouveau_index >= ancien_index
```

### X.6 Documents de référence
`docs/SRS.md` (exigences), `docs/ARCHITECTURE.md` (C4/Arc42), `docs/ADR.md`
(décisions), `docs/ETAT_DU_SYSTEME.md` + `docs/WORKFLOWS.md` (état + anomalies).

---

*Contrat GraphQL complet : introspection du schéma sur `POST /graphql` (GraphiQL
activé). Ce document reflète l'état de `develop`.*
