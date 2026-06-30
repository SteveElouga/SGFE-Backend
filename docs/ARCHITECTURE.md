# Documentation Architecturale
## Système de Gestion de Facturation d'Eau

> **Modèles de référence :** C4 Model (Simon Brown) + Arc42
> **Version :** 1.0.0
> **Date :** Juin 2026
> **Statut :** Validé

---

## Table des matières

1. [Introduction et objectifs](#1-introduction-et-objectifs)
2. [Contraintes architecturales](#2-contraintes-architecturales)
3. [Contexte système — C4 Niveau 1](#3-contexte-système--c4-niveau-1)
4. [Conteneurs — C4 Niveau 2](#4-conteneurs--c4-niveau-2)
5. [Composants par service — C4 Niveau 3](#5-composants-par-service--c4-niveau-3)
6. [Vue Runtime — Flux d'exécution](#6-vue-runtime--flux-dexécution)
7. [Vue Déploiement — Kubernetes](#7-vue-déploiement--kubernetes)
8. [Modèles de données](#8-modèles-de-données)
9. [Contrats gRPC — Fichiers .proto](#9-contrats-grpc--fichiers-proto)
10. [Schéma GraphQL](#10-schéma-graphql)
11. [Concepts transversaux](#11-concepts-transversaux)
12. [Risques et dette technique](#12-risques-et-dette-technique)
13. [Glossaire technique](#13-glossaire-technique)

---

## 1. Introduction et objectifs

### 1.1 Objectif de ce document

Ce document décrit l'architecture technique complète du Système de Gestion de Facturation d'Eau. Il couvre la décomposition en microservices, les patterns de communication, les modèles de données, les contrats d'interface, la stratégie de déploiement et les concepts transversaux (sécurité, logging, gestion des erreurs).

### 1.2 Objectifs de qualité

| Priorité | Attribut | Description |
|---|---|---|
| 1 | **Maintenabilité** | Chaque service est modifiable indépendamment |
| 2 | **Fiabilité** | La panne d'un service n'affecte pas les autres |
| 3 | **Évolutivité** | Nouveaux canaux de notification, nouveaux modules possibles sans refactorisation |
| 4 | **Sécurité** | Données sensibles protégées, accès contrôlés par rôle |
| 5 | **Utilisabilité mobile** | L'interface agent fonctionne sur smartphone de terrain |

### 1.3 Parties prenantes architecturales

| Partie prenante | Intérêt architectural |
|---|---|
| Développeur backend | Structure des services, contrats gRPC, modèles de données |
| Développeur frontend | Schéma GraphQL, types disponibles |
| DevOps | Déploiement Kubernetes, stratégie Canary, monitoring |
| Admin système | Infrastructure MacBook + ngrok, sauvegardes |

---

## 2. Contraintes architecturales

### 2.1 Contraintes techniques imposées

| ID | Contrainte | Impact |
|---|---|---|
| CA-001 | Microservices avec BD séparée par service | Isolation des données, pas de jointures cross-service |
| CA-002 | gRPC pour la communication inter-services | Contrats forts (.proto), performance, typage strict |
| CA-003 | GraphQL comme interface externe unique | Un seul endpoint pour le frontend Angular |
| CA-004 | Django pour tous les services backend | Uniformité du stack, facilité de maintenance |
| CA-005 | PostgreSQL par service | Une instance PostgreSQL par microservice |
| CA-006 | Kubernetes + Minikube pour l'orchestration | Déploiement sur MacBook Pro uniquement |
| CA-007 | Canary Deployment comme stratégie de déploiement | Déploiement progressif avec rollback possible |
| CA-008 | Angular PWA mobile-first | Interface responsive, installable sur smartphone |
| CA-009 | MacBook Pro comme serveur + ngrok | Accès public via tunnel HTTPS |

### 2.2 Contraintes budgétaires

- Hébergement : zéro coût (MacBook Pro + ngrok gratuit)
- WhatsApp : zéro coût (whatsapp-web.js + compte WhatsApp dédié de la régie)
- Toutes les librairies utilisées sont open source

---

## 3. Contexte système — C4 Niveau 1

Ce niveau montre le système dans son environnement : qui l'utilise et avec quels systèmes externes il interagit.

```
┌─────────────────────────────────────────────────────────────────┐
│                         ACTEURS HUMAINS                          │
│                                                                   │
│  [ADMIN]          [AGENT DE TERRAIN]       [COMPTABLE]           │
│  Bureau, PC       Smartphone, terrain       Bureau, PC            │
│                                                                   │
│  [ABONNÉ] (accès passif via WhatsApp et lien tokenisé)          │
└──────────┬───────────────────┬──────────────────┬───────────────┘
           │                   │                  │
           │   HTTPS/GraphQL   │                  │
           ▼                   ▼                  ▼
┌─────────────────────────────────────────────────────────────────┐
│                                                                   │
│          SYSTÈME DE GESTION DE FACTURATION D'EAU                 │
│                  (9 microservices)                                │
│                                                                   │
└───────────────────────────┬─────────────────────────────────────┘
                            │
            ┌───────────────┼───────────────┐
            │               │               │
            ▼               ▼               ▼
    ┌──────────────┐ ┌──────────────┐ ┌──────────────┐
    │   TELNYX     │ │    ngrok     │ │   MacBook    │
    │  WhatsApp    │ │  (tunnel     │ │  (serveur    │
    │    API       │ │   HTTPS)     │ │   physique)  │
    └──────────────┘ └──────────────┘ └──────────────┘
```

---

## 4. Conteneurs — C4 Niveau 2

Ce niveau montre la décomposition technique du système en services déployables.

```
┌─────────────────────────────────────────────────────────────────────┐
│                         ANGULAR PWA                                  │
│              (TypeScript, Angular, Apollo Client)                    │
│              Mobile-first, Progressive Web App                       │
└──────────────────────────────┬──────────────────────────────────────┘
                               │ HTTPS / GraphQL
                               ▼
┌─────────────────────────────────────────────────────────────────────┐
│                         API GATEWAY                                  │
│                    (Django + Strawberry GraphQL)                     │
│                         Pas de base de données                       │
│                                                                      │
│  Responsabilités :                                                   │
│  • Exposer le schéma GraphQL unifié                                  │
│  • Valider le JWT sur chaque requête                                 │
│  • Extraire le rôle (ADMIN / AGENT / COMPTABLE)                      │
│  • Router vers le service gRPC concerné                              │
│  • Agréger les réponses multi-services                               │
│  • Gérer les erreurs globales                                        │
└──────────┬────────────┬────────────┬────────────┬───────────────────┘
           │            │            │            │  gRPC / HTTP2
           ▼            ▼            ▼            ▼
┌──────────────┐ ┌──────────────┐ ┌──────────────┐ ┌──────────────┐
│ AUTH SERVICE │ │ ABONNÉ       │ │ CAMPAGNE     │ │ FACTURATION  │
│              │ │ SERVICE      │ │ SERVICE      │ │ SERVICE      │
│ Django+gRPC  │ │ Django+gRPC  │ │ Django+gRPC  │ │ Django+gRPC  │
│              │ │              │ │              │ │ + ReportLab  │
│ PostgreSQL   │ │ PostgreSQL   │ │ PostgreSQL   │ │ PostgreSQL   │
└──────────────┘ └──────────────┘ └──────────────┘ └──────────────┘

┌──────────────┐ ┌──────────────┐ ┌──────────────┐ ┌──────────────┐
│ PAIEMENT     │ │ NOTIFICATION │ │ REPORTING    │ │ CONFIG       │
│ SERVICE      │ │ SERVICE      │ │ SERVICE      │ │ SERVICE      │
│              │ │              │ │              │ │              │
│ Django+gRPC  │ │ Django+gRPC  │ │ Django+gRPC  │ │ Django+gRPC  │
│ + cron jobs  │ │ + Telnyx API │ │ Read-only    │ │              │
│ PostgreSQL   │ │ PostgreSQL   │ │ PostgreSQL   │ │ PostgreSQL   │
└──────────────┘ └──────────────┘ └──────────────┘ └──────────────┘
```

### 4.1 Récapitulatif des services

| Service | Rôle | BD | Port gRPC | Port HTTP |
|---|---|---|---|---|
| API Gateway | Point d'entrée GraphQL | Aucune | N/A | 8000 |
| Auth Service | Identité, JWT, rôles | PostgreSQL auth_db | 50051 | — |
| Abonné Service | Abonnés + compteurs | PostgreSQL abonne_db | 50052 | — |
| Campagne Service | Campagnes + relevés | PostgreSQL campagne_db | 50053 | — |
| Facturation Service | Factures + PDF | PostgreSQL facturation_db | 50054 | — |
| Paiement Service | Paiements + impayés | PostgreSQL paiement_db | 50055 | — |
| Notification Service | Tokens abonnés | PostgreSQL notification_db | 50056 | — |
| Reporting Service | Agrégateur read-only | PostgreSQL reporting_db | 50057 | — |
| Config Service | Paramètres système | PostgreSQL config_db | 50058 | — |
| WhatsApp Service | Envoi WhatsApp (whatsapp-web.js) | Aucune | N/A | 3000 |

---

## 5. Composants par service — C4 Niveau 3

### 5.1 API Gateway

```
API Gateway
├── GraphQL Schema (Strawberry)
│     └── Fédère les types de tous les services
├── JWT Middleware
│     └── Valide chaque requête entrante
├── Role Middleware
│     └── Vérifie les permissions par rôle
├── gRPC Client Pool
│     └── Connexions gRPC vers chaque service
├── Query Resolvers
│     └── Traduit les queries GraphQL en appels gRPC
├── Mutation Resolvers
│     └── Traduit les mutations GraphQL en appels gRPC
└── Error Handler
      └── Normalise les erreurs gRPC en erreurs GraphQL
```

### 5.2 Auth Service

```
Auth Service
├── gRPC Server
│     └── Implémente AuthServiceServicer
├── Domain Layer
│     ├── UserManager (création, modification, désactivation)
│     └── TokenManager (JWT, refresh token, blacklist)
├── Security Layer
│     ├── PasswordHasher (bcrypt)
│     └── BruteForceProtection (compteur tentatives)
└── Repository Layer
      └── UserRepository (CRUD PostgreSQL)
```

### 5.3 Abonné Service

```
Abonné Service
├── gRPC Server
│     └── Implémente AbonneServiceServicer
├── Domain Layer
│     ├── AbonneManager (CRUD, suspension, réactivation)
│     ├── CompteurManager (création, remplacement, archivage)
│     └── NumerotationService (génération AB-XXXX)
├── Event Emitter
│     └── Émet AbonneCreated, AbonneSuspendu, CompteurRemplace
└── Repository Layer
      ├── AbonneRepository
      └── CompteurRepository
```

### 5.4 Campagne Service

```
Campagne Service
├── gRPC Server
│     └── Implémente CampagneServiceServicer
├── Domain Layer
│     ├── CampagneManager (création, clôture, planification)
│     ├── ReleveManager (saisie index, calcul consommation)
│     └── ProgressionCalculator
├── Scheduler (cron)
│     └── CampagnePlanifieeJob (vérification J-1 et J)
├── Event Emitter
│     └── Émet CampagneCree, IndexSaisi, CampagneCloturee
├── Event Consumer
│     └── Consomme AbonneCreated
└── Repository Layer
      ├── CampagneRepository
      └── ReleveRepository
```

### 5.5 Facturation Service

```
Facturation Service
├── gRPC Server
│     └── Implémente FacturationServiceServicer
├── Domain Layer
│     ├── FactureManager (génération, numérotation, statut)
│     ├── TarifManager (CRUD tarif, historique)
│     └── MontantCalculator (consommation × prix_m3)
├── PDF Generator
│     └── ReportLab (génération PDF avec logo, données)
├── Event Emitter
│     └── Émet FactureGeneree, PDFGenere
├── Event Consumer
│     └── Consomme CampagneCloturee
└── Repository Layer
      ├── FactureRepository
      └── TarifRepository
```

### 5.6 Paiement Service

```
Paiement Service
├── gRPC Server
│     └── Implémente PaiementServiceServicer
├── Domain Layer
│     ├── PaiementManager (enregistrement, validation)
│     ├── SoldeCalculator (calcul solde restant, statut)
│     └── ImpayeManager (suivi, étapes de relance)
├── Scheduler (cron)
│     └── ImpayeCheckerJob (vérification quotidienne à 8h00)
├── Event Emitter
│     └── Émet PaiementEnregistre, RelanceRequise, SuspensionRequise
├── Event Consumer
│     └── Consomme FactureGeneree
└── Repository Layer
      ├── PaiementRepository
      ├── SoldeFactureRepository
      └── SuiviImpayeRepository
```

### 5.7 Notification Service

```
Notification Service
├── gRPC Server
│     └── Implémente NotificationServiceServicer
├── Domain Layer
│     ├── EnvoiManager (orchestration des envois)
│     ├── TokenManager (génération, validation, révocation UUID)
│     └── MessageBuilder (construction des messages WhatsApp)
├── Telnyx Adapter
│     └── TelnyxClient (envoi message texte + PDF)
├── Retry Handler
│     └── 3 tentatives automatiques en cas d'échec
├── Event Consumer
│     └── Consomme FactureGeneree, RelanceRequise, CampagnePlanifieeJ1, CampagnePlanifieeJ, SuspensionRequise
└── Repository Layer
      ├── EnvoiRepository
      └── TokenAccesRepository
```

### 5.8 Reporting Service

```
Reporting Service
├── gRPC Server
│     └── Implémente ReportingServiceServicer
├── Domain Layer
│     └── AgregateurDashboard (compilation des stats)
├── Event Consumer (tous les événements)
│     ├── Consomme CampagneCloturee → met à jour StatsCampagne
│     ├── Consomme FactureGeneree → met à jour StatsFacturation
│     ├── Consomme FactureEnvoyee → met à jour StatsFacturation
│     └── Consomme PaiementEnregistre → met à jour StatsPaiements
└── Repository Layer
      ├── StatsCampagneRepository
      ├── StatsFacturationRepository
      └── StatsPaiementsRepository
```

### 5.9 Config Service

```
Config Service
├── gRPC Server
│     └── Implémente ConfigServiceServicer
├── Domain Layer
│     ├── InfosSocieteManager
│     └── ConfigManager (get/set clés de configuration)
└── Repository Layer
      ├── InfosSocieteRepository
      └── ConfigAppRepository
```

---

## 6. Vue Runtime — Flux d'exécution

### 6.1 Flux — Connexion d'un utilisateur

```
1. Angular envoie mutation GraphQL login(identifier, password)
   identifier accepte un nom d'utilisateur OU un numéro de téléphone (+237XXXXXXXXX)
2. API Gateway reçoit la requête (pas de JWT requis pour login)
3. Gateway appelle Auth.Login via gRPC
4. Auth Service résout l'identifiant (username OU phone_number) et vérifie les credentials
5. Si valides : génère JWT (24h) + refresh token (7j)
6. Retourne les tokens au Gateway
7. Gateway pose le refresh token en cookie HttpOnly + Secure + SameSite=Strict
   (jamais exposé à JS), et retourne AuthPayload (accessToken, expiresIn,
   user) à Angular — sans le refreshToken
8. Angular stocke l'accessToken en mémoire (jamais en localStorage)
9. Au-delà de l'expiration de l'access token : mutation refreshToken (sans
   argument, le cookie est envoyé automatiquement par le navigateur) →
   Gateway lit le cookie, appelle Auth.RefreshToken, repose un nouveau
   cookie (rotation), retourne un nouvel AuthPayload
```

### 6.2 Flux — Saisie d'un index (Agent terrain)

```
1. Agent ouvre l'app sur smartphone → JWT dans le header
2. Angular envoie query GraphQL releves(campagneId)
3. Gateway valide JWT → rôle AGENT → autorisé
4. Gateway appelle Campagne.ListReleves via gRPC
5. Campagne Service retourne la liste des abonnés
6. Agent choisit un abonné et saisit le nouvel index
7. Angular envoie mutation GraphQL saisirIndex(input)
8. Gateway valide JWT + rôle AGENT
9. Gateway appelle Campagne.SaisirIndex via gRPC
10. Campagne Service valide (nouveau >= ancien)
11. Campagne Service calcule la consommation
12. Campagne Service enregistre le relevé
13. Campagne Service émet événement IndexSaisi
14. Reporting Service consomme → met à jour progression
15. Retour confirmation à Angular
```

### 6.3 Flux — Clôture de campagne et génération des factures

```
1. Admin clique "Clôturer la campagne"
2. Angular envoie mutation cloturerCampagne(id)
3. Gateway → Campagne.CloturerCampagne via gRPC
4. Campagne Service change statut → CLOTUREE
5. Campagne Service émet événement CampagneCloturee

6. Facturation Service consomme CampagneCloturee
7. Pour chaque relevé de la campagne :
   a. Récupère les infos abonné via gRPC → Abonné Service
   b. Récupère le tarif actif via repository interne
   c. Calcule le montant (consommation × prix_m3)
   d. Crée la facture en base
   e. Génère le PDF via ReportLab
   f. Enregistre le chemin PDF
   g. Émet événement FactureGeneree

8. Paiement Service consomme FactureGeneree
   → Crée le SoldeFacture initial (montant_paye = 0, IMPAYEE)

9. Reporting Service consomme FactureGeneree
   → Met à jour StatsFacturation
```

### 6.4 Flux — Envoi WhatsApp d'une facture

```
1. Déclenchement : automatique (après génération) ou manuel (admin/comptable)
2. Gateway → Notification.EnvoyerFacture(factureId) via gRPC
3. Notification Service récupère :
   a. Les infos de la facture via gRPC → Facturation Service
   b. Le numéro WhatsApp de l'abonné via gRPC → Abonné Service
4. Génère un token UUID v4
5. Calcule la date d'expiration (date_envoi + 20 jours)
6. Construit le message WhatsApp (texte structuré avec détails de la facture,
   sans PDF — le PDF reste disponible côté backoffice uniquement)
7. Appelle le WhatsApp Service via HTTP POST /send
   (whatsapp-service Node.js — whatsapp-web.js, compte dédié de la régie)
8. Si succès : enregistre l'envoi (statut ENVOYE)
9. Si échec : retry (3 fois max), puis statut ECHEC + notification admin
10. Émet événement FactureEnvoyee
11. Reporting Service consomme → met à jour StatsFacturation
```

### 6.5 Flux — Gestion des impayés (cron quotidien)

```
Chaque jour à 8h00 — ImpayeCheckerJob s'exécute :

Pour chaque SuiviImpaye non résolu :

  Calcule jours_depuis_depassement

  Si jours >= delai_rappel_1 ET rappel_1_envoye = false :
    → Émet RelanceRequise(etape=1, factureId)
    → rappel_1_envoye = true

  Si jours >= delai_rappel_2 ET rappel_2_envoye = false :
    → Émet RelanceRequise(etape=2, factureId)
    → rappel_2_envoye = true

  Si jours >= delai_avertissement ET avertissement_envoye = false :
    → Émet RelanceRequise(etape=3, factureId)
    → Notifie les admins
    → avertissement_envoye = true

  Si jours >= delai_suspension ET suspension_effectuee = false
     ET suspension_auto = true :
    → Émet SuspensionRequise(abonneId)
    → Émet RelanceRequise(etape=4, factureId)
    → suspension_effectuee = true

Notification Service consomme RelanceRequise :
  → Construit le message correspondant à l'étape
  → Envoie via WhatsApp Service (HTTP POST /send)

Abonné Service consomme SuspensionRequise :
  → Passe l'abonné en statut SUSPENDU
```

### 6.6 Flux — Paiement d'une facture

```
1. Comptable saisit le paiement dans l'interface
2. Angular envoie mutation enregistrerPaiement(input)
3. Gateway → Paiement.EnregistrerPaiement via gRPC
4. Paiement Service valide les données
5. Crée le Paiement en base
6. Met à jour SoldeFacture :
   montant_paye += versement
   solde_restant = montant_total - montant_paye
   Calcule le nouveau statut (PARTIELLE ou PAYEE)
7. Si statut = PAYEE :
   a. SuiviImpaye.resolu_le = aujourd'hui
   b. Si abonné SUSPENDU → émet RétablissementRequis
   c. Abonné Service consomme → statut ACTIF
   d. Notification Service envoie WhatsApp de confirmation
8. Émet PaiementEnregistre
9. Reporting Service consomme → met à jour StatsPaiements
```

### 6.7 Flux — Création de compte et activation

Le flux d'activation dépend du rôle. Tous les rôles nécessitent un numéro
de téléphone camerounais (+2376XXXXXXXX). L'e-mail n'est obligatoire que
pour le rôle ADMIN.

**ADMIN — activation par e-mail (Brevo)**
```
1. Admin envoie mutation createUser(username, email, phoneNumber, role=ADMIN)
2. Gateway vérifie le rôle ADMIN, appelle Auth.CreateUser via gRPC
3. Auth Service crée l'utilisateur (set_unusable_password),
   génère un PasswordSetupToken (validité 48h)
4. Auth Service envoie un e-mail Brevo avec lien
   {FRONTEND_URL}/set-password?token=...
5. Utilisateur clique le lien → mutation activateAccount(token, password)
6. Gateway → Auth.SetPasswordWithToken → mot de passe défini
7. L'utilisateur peut se connecter via login (flux 6.1)
```

**AGENT / COMPTABLE / SUPERVISEUR — activation par OTP WhatsApp**
```
1. Admin envoie mutation createUser(username, phoneNumber, role)
2. Gateway vérifie le rôle ADMIN, appelle Auth.CreateUser via gRPC
3. Auth Service crée l'utilisateur (set_unusable_password),
   génère un PhoneOtpToken à 6 chiffres (validité 10 min)
4. Auth Service appelle WhatsApp Service (HTTP) → envoie l'OTP au phoneNumber
5. Utilisateur reçoit l'OTP sur WhatsApp → saisit le code dans l'app
   → mutation verifyOtpAndSetPassword(phoneNumber, otpCode, password)
6. Gateway → Auth.VerifyOtpAndSetPassword → OTP vérifié, mot de passe défini
7. L'utilisateur peut se connecter via login (identifiant = username ou téléphone)
```

**Réinitialisation du mot de passe**

| Rôle | Méthode |
|---|---|
| ADMIN | `requestPasswordReset(email)` → lien Brevo → `resetPassword(token, password)` |
| Autres | `requestPhoneOtp(phoneNumber)` → OTP WhatsApp → `verifyOtpAndSetPassword(...)` |

Les deux flux retournent toujours `true` côté client, sans révéler si
l'identifiant existe (protection contre l'énumération de comptes).

---

## 7. Vue Déploiement — Kubernetes

### 7.1 Architecture Kubernetes sur MacBook

```
MACBOOK PRO (serveur physique)
│
├── Minikube
│     │
│     ├── Namespace: facturation-eau
│     │
│     ├── Deployments (1 par service)
│     │     ├── api-gateway-deployment       (1 pod)
│     │     ├── auth-deployment              (1 pod)
│     │     ├── abonne-deployment            (1 pod)
│     │     ├── campagne-deployment          (1 pod)
│     │     ├── facturation-deployment       (1 pod)
│     │     ├── paiement-deployment          (1 pod)
│     │     ├── notification-deployment      (1 pod)
│     │     ├── reporting-deployment         (1 pod)
│     │     └── config-deployment            (1 pod)
│     │
│     ├── Services (ClusterIP pour gRPC, NodePort pour Gateway)
│     │     ├── api-gateway-service          (NodePort 8000)
│     │     ├── auth-service                 (ClusterIP 50051)
│     │     ├── abonne-service               (ClusterIP 50052)
│     │     ├── campagne-service             (ClusterIP 50053)
│     │     ├── facturation-service          (ClusterIP 50054)
│     │     ├── paiement-service             (ClusterIP 50055)
│     │     ├── notification-service         (ClusterIP 50056)
│     │     ├── reporting-service            (ClusterIP 50057)
│     │     └── config-service               (ClusterIP 50058)
│     │
│     ├── StatefulSets (1 PostgreSQL par service)
│     │     ├── auth-postgres
│     │     ├── abonne-postgres
│     │     ├── campagne-postgres
│     │     ├── facturation-postgres
│     │     ├── paiement-postgres
│     │     ├── notification-postgres
│     │     ├── reporting-postgres
│     │     └── config-postgres
│     │
│     ├── PersistentVolumeClaims (1 par PostgreSQL)
│     │     └── [service]-postgres-pvc
│     │
│     ├── ConfigMaps (configuration non-sensible)
│     │     └── app-config
│     │
│     └── Secrets (données sensibles)
│           ├── postgres-credentials
│           ├── jwt-secret
│           └── telnyx-api-key
│
└── ngrok
      └── Tunnel HTTPS → api-gateway-service:8000
            → URL publique accessible depuis Internet
```

### 7.2 Stratégie Canary Deployment

Le Canary Deployment est implémenté avec des labels Kubernetes.

**Étape 1 — État initial (100% v1) :**
```yaml
# Deployment v1 — 1 replica
apiVersion: apps/v1
kind: Deployment
metadata:
  name: api-gateway-v1
spec:
  replicas: 1
  selector:
    matchLabels:
      app: api-gateway
      version: v1
```

**Étape 2 — Déploiement Canary (10% v2) :**
```yaml
# Deployment v2 (canary) — 1 replica sur 10 total
apiVersion: apps/v1
kind: Deployment
metadata:
  name: api-gateway-v2-canary
spec:
  replicas: 1
  selector:
    matchLabels:
      app: api-gateway
      version: v2
---
# Service qui route vers les deux versions
apiVersion: v1
kind: Service
metadata:
  name: api-gateway-service
spec:
  selector:
    app: api-gateway   # sélectionne v1 ET v2
  ports:
    - port: 8000
```

**Étape 3 — Promotion progressive :**
```
v1: 9 replicas, v2: 1 replica  → 10% canary
v1: 5 replicas, v2: 5 replicas → 50%
v1: 0 replicas, v2: 1 replica  → 100% → v1 supprimée
```

**Rollback immédiat si problème :**
```bash
kubectl scale deployment api-gateway-v2-canary --replicas=0
# v1 reprend 100% du trafic instantanément
```

---

## 8. Modèles de données

### 8.1 Auth Service — auth_db

```sql
-- Utilisateurs du système
CREATE TABLE users (
    id              UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    username        VARCHAR(100) UNIQUE NOT NULL,
    -- Obligatoire pour ADMIN (activation + reset par e-mail), NULL pour les autres rôles
    email           VARCHAR(255) UNIQUE,
    -- Obligatoire pour tous les rôles (+2376XXXXXXXX) — login et OTP WhatsApp
    phone_number    VARCHAR(20) UNIQUE,
    password_hash   VARCHAR(255) NOT NULL,
    role            VARCHAR(20) NOT NULL CHECK (role IN ('ADMIN', 'AGENT', 'COMPTABLE', 'SUPERVISEUR')),
    is_active       BOOLEAN DEFAULT TRUE,
    failed_attempts INTEGER DEFAULT 0,
    locked_until    TIMESTAMP WITH TIME ZONE,
    created_at      TIMESTAMP WITH TIME ZONE DEFAULT NOW(),
    updated_at      TIMESTAMP WITH TIME ZONE DEFAULT NOW()
);
-- password_hash est inutilisable (set_unusable_password) jusqu'à ce que
-- l'utilisateur définisse son mot de passe via le lien d'activation (ADMIN)
-- ou l'OTP WhatsApp (autres rôles) — voir flux 6.7.

-- Tokens révoqués (blacklist JWT)
CREATE TABLE revoked_tokens (
    id          UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    token_jti   VARCHAR(255) UNIQUE NOT NULL,
    revoked_at  TIMESTAMP WITH TIME ZONE DEFAULT NOW(),
    expires_at  TIMESTAMP WITH TIME ZONE NOT NULL
);

-- Activation de compte / reset de mot de passe par e-mail (ADMIN uniquement, via Brevo)
CREATE TABLE password_setup_tokens (
    id          UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    user_id     UUID NOT NULL REFERENCES users(id) ON DELETE CASCADE,
    token       VARCHAR(64) UNIQUE NOT NULL,
    created_at  TIMESTAMP WITH TIME ZONE DEFAULT NOW(),
    expires_at  TIMESTAMP WITH TIME ZONE NOT NULL,
    used_at     TIMESTAMP WITH TIME ZONE
);

-- OTP WhatsApp pour activation et reset (tous les rôles, via WhatsApp Service)
-- Le code est stocké haché — jamais en clair
CREATE TABLE phone_otp_tokens (
    id          UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    user_id     UUID NOT NULL REFERENCES users(id) ON DELETE CASCADE,
    otp_hash    VARCHAR(255) NOT NULL,
    created_at  TIMESTAMP WITH TIME ZONE DEFAULT NOW(),
    expires_at  TIMESTAMP WITH TIME ZONE NOT NULL,  -- validité 10 min
    used_at     TIMESTAMP WITH TIME ZONE
);
```

### 8.2 Abonné Service — abonne_db

```sql
-- Abonnés
CREATE TABLE abonnes (
    id                  UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    numero_abonne       VARCHAR(10) UNIQUE NOT NULL,  -- AB-XXXX
    nom                 VARCHAR(100) NOT NULL,
    prenom              VARCHAR(100) NOT NULL,
    telephone_whatsapp  VARCHAR(20) NOT NULL,
    adresse             TEXT,
    statut              VARCHAR(20) NOT NULL DEFAULT 'ACTIF'
                        CHECK (statut IN ('ACTIF', 'SUSPENDU', 'RESILIE')),
    created_at          TIMESTAMP WITH TIME ZONE DEFAULT NOW(),
    updated_at          TIMESTAMP WITH TIME ZONE DEFAULT NOW()
);

-- Compteurs
CREATE TABLE compteurs (
    id              UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    abonne_id       UUID NOT NULL REFERENCES abonnes(id),
    numero_compteur INTEGER NOT NULL,
    quartier        VARCHAR(100) NOT NULL,
    camp            INTEGER NOT NULL,
    index_initial   DECIMAL(10, 3) NOT NULL DEFAULT 0,
    date_pose       DATE NOT NULL,
    statut          VARCHAR(20) NOT NULL DEFAULT 'ACTIF'
                    CHECK (statut IN ('ACTIF', 'REMPLACE', 'DESACTIVE')),
    created_at      TIMESTAMP WITH TIME ZONE DEFAULT NOW()
);

-- Historique des remplacements de compteur
CREATE TABLE historique_compteurs (
    id                    UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    abonne_id             UUID NOT NULL REFERENCES abonnes(id),
    ancien_compteur_id    UUID NOT NULL REFERENCES compteurs(id),
    nouveau_compteur_id   UUID NOT NULL REFERENCES compteurs(id),
    index_fermeture       DECIMAL(10, 3) NOT NULL,
    date_remplacement     DATE NOT NULL,
    created_at            TIMESTAMP WITH TIME ZONE DEFAULT NOW()
);

CREATE INDEX idx_compteurs_abonne ON compteurs(abonne_id);
CREATE INDEX idx_abonnes_statut ON abonnes(statut);
```

### 8.3 Campagne Service — campagne_db

```sql
-- Campagnes de relevé
CREATE TABLE campagnes (
    id                      UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    nom                     VARCHAR(100) NOT NULL,
    periode_mois            INTEGER NOT NULL CHECK (periode_mois BETWEEN 1 AND 12),
    periode_annee           INTEGER NOT NULL,
    statut                  VARCHAR(20) NOT NULL DEFAULT 'BROUILLON'
                            CHECK (statut IN ('BROUILLON', 'EN_COURS', 'CLOTUREE')),
    date_planifiee          DATE,
    notification_j1_sent    BOOLEAN DEFAULT FALSE,
    notification_j_sent     BOOLEAN DEFAULT FALSE,
    date_creation           TIMESTAMP WITH TIME ZONE DEFAULT NOW(),
    date_cloture            TIMESTAMP WITH TIME ZONE,
    created_by              UUID NOT NULL,  -- user_id (Auth Service)
    UNIQUE(periode_mois, periode_annee)
);

-- Relevés d'index
CREATE TABLE releves (
    id              UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    campagne_id     UUID NOT NULL REFERENCES campagnes(id),
    abonne_id       UUID NOT NULL,  -- référence Abonné Service
    ancien_index    DECIMAL(10, 3) NOT NULL,
    nouveau_index   DECIMAL(10, 3),
    consommation    DECIMAL(10, 3),  -- calculé : nouveau - ancien
    date_releve     TIMESTAMP WITH TIME ZONE,
    agent_id        UUID,  -- user_id (Auth Service)
    observation     TEXT,
    statut          VARCHAR(20) NOT NULL DEFAULT 'A_RELEVER'
                    CHECK (statut IN ('A_RELEVER', 'RELEVE', 'ESTIME', 'NON_RELEVE')),
    created_at      TIMESTAMP WITH TIME ZONE DEFAULT NOW(),
    updated_at      TIMESTAMP WITH TIME ZONE DEFAULT NOW(),
    UNIQUE(campagne_id, abonne_id)
);

CREATE INDEX idx_releves_campagne ON releves(campagne_id);
CREATE INDEX idx_releves_statut ON releves(statut);
CREATE INDEX idx_releves_abonne ON releves(abonne_id);
```

### 8.4 Facturation Service — facturation_db

```sql
-- Historique des tarifs
CREATE TABLE tarifs (
    id              UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    prix_m3         DECIMAL(10, 2) NOT NULL,  -- 500.00 FCFA
    abonnement_fixe DECIMAL(10, 2) NOT NULL DEFAULT 0,
    date_effet      DATE NOT NULL,
    is_active       BOOLEAN DEFAULT TRUE,
    created_at      TIMESTAMP WITH TIME ZONE DEFAULT NOW()
);

-- Factures
CREATE TABLE factures (
    id                      UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    numero_facture          VARCHAR(30) UNIQUE NOT NULL,  -- FACT-AAAA-MM-XXXX
    abonne_id               UUID NOT NULL,  -- référence Abonné Service
    campagne_id             UUID NOT NULL,  -- référence Campagne Service
    releve_id               UUID NOT NULL,  -- référence Campagne Service
    ancien_index            DECIMAL(10, 3) NOT NULL,
    nouveau_index           DECIMAL(10, 3) NOT NULL,
    consommation            DECIMAL(10, 3) NOT NULL,
    prix_m3                 DECIMAL(10, 2) NOT NULL,  -- copié du tarif actif
    montant                 DECIMAL(12, 2) NOT NULL,
    statut                  VARCHAR(20) NOT NULL DEFAULT 'GENEREE'
                            CHECK (statut IN ('GENEREE', 'ENVOYEE', 'PAYEE', 'PARTIELLE', 'IMPAYEE')),
    date_releve             DATE NOT NULL,
    date_limite_paiement    DATE NOT NULL,
    date_generation         TIMESTAMP WITH TIME ZONE DEFAULT NOW(),
    pdf_path                TEXT
);

CREATE INDEX idx_factures_abonne ON factures(abonne_id);
CREATE INDEX idx_factures_campagne ON factures(campagne_id);
CREATE INDEX idx_factures_statut ON factures(statut);
```

### 8.5 Paiement Service — paiement_db

```sql
-- Paiements (versements)
CREATE TABLE paiements (
    id                      UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    facture_id              UUID NOT NULL,  -- référence Facturation Service
    abonne_id               UUID NOT NULL,  -- référence Abonné Service
    montant                 DECIMAL(12, 2) NOT NULL,
    date_paiement           DATE NOT NULL,
    mode_paiement           VARCHAR(20) NOT NULL
                            CHECK (mode_paiement IN ('ESPECES', 'MOBILE_MONEY', 'VIREMENT')),
    reference_transaction   VARCHAR(100),  -- obligatoire si MOBILE_MONEY ou VIREMENT
    enregistre_par          UUID NOT NULL,  -- user_id (Auth Service)
    created_at              TIMESTAMP WITH TIME ZONE DEFAULT NOW()
);

-- Solde par facture (dénormalisé pour performance)
CREATE TABLE soldes_factures (
    facture_id          UUID PRIMARY KEY,  -- référence Facturation Service
    montant_total       DECIMAL(12, 2) NOT NULL,
    montant_paye        DECIMAL(12, 2) NOT NULL DEFAULT 0,
    solde_restant       DECIMAL(12, 2) NOT NULL,
    statut              VARCHAR(20) NOT NULL DEFAULT 'IMPAYEE'
                        CHECK (statut IN ('IMPAYEE', 'PARTIELLE', 'PAYEE')),
    updated_at          TIMESTAMP WITH TIME ZONE DEFAULT NOW()
);

-- Suivi des impayés
CREATE TABLE suivis_impayes (
    id                          UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    facture_id                  UUID NOT NULL UNIQUE,
    abonne_id                   UUID NOT NULL,
    date_depassement            DATE NOT NULL,
    etape_actuelle              INTEGER NOT NULL DEFAULT 1 CHECK (etape_actuelle BETWEEN 1 AND 4),
    rappel_1_envoye             BOOLEAN DEFAULT FALSE,
    date_rappel_1               TIMESTAMP WITH TIME ZONE,
    rappel_2_envoye             BOOLEAN DEFAULT FALSE,
    date_rappel_2               TIMESTAMP WITH TIME ZONE,
    avertissement_envoye        BOOLEAN DEFAULT FALSE,
    date_avertissement          TIMESTAMP WITH TIME ZONE,
    suspension_effectuee        BOOLEAN DEFAULT FALSE,
    date_suspension             TIMESTAMP WITH TIME ZONE,
    relances_suspendues_jusqu   DATE,  -- suspension des relances après versement partiel
    resolu_le                   DATE,
    created_at                  TIMESTAMP WITH TIME ZONE DEFAULT NOW()
);

CREATE INDEX idx_paiements_facture ON paiements(facture_id);
CREATE INDEX idx_suivis_impayes_resolu ON suivis_impayes(resolu_le) WHERE resolu_le IS NULL;
```

### 8.6 Notification Service — notification_db

```sql
-- Historique des envois WhatsApp
CREATE TABLE envois (
    id                  UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    facture_id          UUID NOT NULL,  -- référence Facturation Service
    abonne_id           UUID NOT NULL,  -- référence Abonné Service
    type_envoi          VARCHAR(30) NOT NULL,  -- FACTURE, RELANCE_1, RELANCE_2, AVERTISSEMENT, SUSPENSION, RETABLISSEMENT
    telephone           VARCHAR(20) NOT NULL,
    statut              VARCHAR(20) NOT NULL DEFAULT 'EN_ATTENTE'
                        CHECK (statut IN ('EN_ATTENTE', 'ENVOYE', 'ECHEC')),
    date_envoi          TIMESTAMP WITH TIME ZONE,
    telnyx_message_id   VARCHAR(100),
    erreur              TEXT,
    tentatives          INTEGER DEFAULT 0,
    created_at          TIMESTAMP WITH TIME ZONE DEFAULT NOW()
);

-- Tokens d'accès à l'espace abonné
CREATE TABLE tokens_acces (
    id                      UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    abonne_id               UUID NOT NULL,
    facture_id              UUID NOT NULL,
    token                   UUID UNIQUE NOT NULL DEFAULT gen_random_uuid(),
    date_expiration         DATE NOT NULL,
    date_derniere_visite    TIMESTAMP WITH TIME ZONE,
    is_active               BOOLEAN DEFAULT TRUE,
    created_at              TIMESTAMP WITH TIME ZONE DEFAULT NOW()
);

CREATE INDEX idx_tokens_acces_token ON tokens_acces(token);
CREATE INDEX idx_tokens_acces_abonne ON tokens_acces(abonne_id);
CREATE INDEX idx_envois_facture ON envois(facture_id);
```

### 8.7 Reporting Service — reporting_db

```sql
-- Statistiques par campagne
CREATE TABLE stats_campagnes (
    campagne_id             UUID PRIMARY KEY,
    nom_campagne            VARCHAR(100) NOT NULL,
    total_abonnes           INTEGER DEFAULT 0,
    nb_releves              INTEGER DEFAULT 0,
    nb_en_attente           INTEGER DEFAULT 0,
    nb_estimes              INTEGER DEFAULT 0,
    nb_non_releves          INTEGER DEFAULT 0,
    pourcentage_progression DECIMAL(5, 2) DEFAULT 0,
    consommation_totale     DECIMAL(12, 3) DEFAULT 0,
    updated_at              TIMESTAMP WITH TIME ZONE DEFAULT NOW()
);

-- Statistiques de facturation par campagne
CREATE TABLE stats_facturation (
    campagne_id             UUID PRIMARY KEY,
    total_factures          INTEGER DEFAULT 0,
    montant_total_facture   DECIMAL(14, 2) DEFAULT 0,
    nb_factures_envoyees    INTEGER DEFAULT 0,
    nb_factures_payees      INTEGER DEFAULT 0,
    nb_factures_partielles  INTEGER DEFAULT 0,
    nb_factures_impayees    INTEGER DEFAULT 0,
    updated_at              TIMESTAMP WITH TIME ZONE DEFAULT NOW()
);

-- Statistiques de paiement par campagne
CREATE TABLE stats_paiements (
    campagne_id         UUID PRIMARY KEY,
    montant_encaisse    DECIMAL(14, 2) DEFAULT 0,
    montant_impaye      DECIMAL(14, 2) DEFAULT 0,
    nb_impayes          INTEGER DEFAULT 0,
    taux_recouvrement   DECIMAL(5, 2) DEFAULT 0,
    updated_at          TIMESTAMP WITH TIME ZONE DEFAULT NOW()
);
```

### 8.8 Config Service — config_db

```sql
-- Informations de la société
CREATE TABLE infos_societe (
    id          UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    nom         VARCHAR(200) NOT NULL,
    adresse     TEXT,
    telephone   VARCHAR(20),
    logo_path   TEXT,
    updated_at  TIMESTAMP WITH TIME ZONE DEFAULT NOW()
);

-- Configuration applicative (clé-valeur)
CREATE TABLE config_app (
    id          UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    cle         VARCHAR(100) UNIQUE NOT NULL,
    valeur      TEXT NOT NULL,
    description TEXT,
    updated_at  TIMESTAMP WITH TIME ZONE DEFAULT NOW()
);

-- Valeurs par défaut insérées au démarrage :
-- telnyx_api_key            → (à configurer)
-- whatsapp_numero           → (à configurer)
-- delai_paiement_jours      → 5
-- token_validite_jours      → 20
-- impaye_delai_rappel_1     → 0
-- impaye_delai_rappel_2     → 3
-- impaye_delai_avertissement → 7
-- impaye_delai_suspension   → 10
-- impaye_suspension_auto    → true
-- impaye_suspension_relances → 5
```

---

## 9. Contrats gRPC — Fichiers .proto

### 9.1 auth_service.proto

```protobuf
syntax = "proto3";
package auth;

service AuthService {
  // identifier accepte un username ou un numéro de téléphone (+237XXXXXXXXX)
  rpc Login (LoginRequest) returns (TokenResponse);
  rpc ValidateToken (TokenRequest) returns (UserPayload);
  rpc RefreshToken (RefreshRequest) returns (TokenResponse);
  rpc Logout (TokenRequest) returns (StatusResponse);
  rpc CreateUser (CreateUserRequest) returns (UserResponse);
  rpc UpdateUser (UpdateUserRequest) returns (UserResponse);
  rpc DeactivateUser (UserIdRequest) returns (UserResponse);
  rpc ListUsers (EmptyRequest) returns (ListUsersResponse);
  rpc GetUser (UserIdRequest) returns (UserResponse);
  // Reset mot de passe ADMIN uniquement (lien e-mail Brevo)
  rpc RequestPasswordReset (EmailRequest) returns (StatusResponse);
  rpc SetPasswordWithToken (SetPasswordRequest) returns (StatusResponse);
  // Activation et reset par OTP WhatsApp (tous les rôles)
  rpc RequestPhoneOtp (PhoneRequest) returns (StatusResponse);
  rpc VerifyOtpAndSetPassword (VerifyOtpRequest) returns (StatusResponse);
}

message LoginRequest {
  // Accepte un nom d'utilisateur ou un numéro de téléphone (+237XXXXXXXXX)
  string identifier = 1;
  string password = 2;
}

message TokenResponse {
  string access_token = 1;
  string refresh_token = 2;
  int64 expires_in = 3;
}

message TokenRequest {
  string token = 1;
}

message RefreshRequest {
  string refresh_token = 1;
}

message UserPayload {
  string user_id = 1;
  string username = 2;
  string email = 3;
  string role = 4;
  bool is_active = 5;
  string phone_number = 6;
}

message CreateUserRequest {
  string username = 1;
  // Obligatoire pour ADMIN, ignoré pour les autres rôles
  string email = 2;
  string role = 3;
  // Obligatoire pour tous les rôles (+2376XXXXXXXX)
  string phone_number = 4;
}

message EmailRequest {
  string email = 1;
}

message PhoneRequest {
  string phone_number = 1;
}

message SetPasswordRequest {
  string token = 1;
  string new_password = 2;
}

message VerifyOtpRequest {
  string phone_number = 1;
  string otp_code = 2;
  string new_password = 3;
}

message UpdateUserRequest {
  string user_id = 1;
  string email = 2;
  string role = 3;
  string phone_number = 4;
}

message UserIdRequest {
  string user_id = 1;
}

message UserResponse {
  string user_id = 1;
  string username = 2;
  string email = 3;
  string role = 4;
  bool is_active = 5;
  string created_at = 6;
  string phone_number = 7;
}

message ListUsersResponse {
  repeated UserResponse users = 1;
}

message StatusResponse {
  bool success = 1;
  string message = 2;
}

message EmptyRequest {}
```

### 9.2 abonne_service.proto

```protobuf
syntax = "proto3";
package abonne;

service AbonneService {
  rpc GetAbonne (AbonneIdRequest) returns (AbonneResponse);
  rpc ListAbonnes (ListAbonnesRequest) returns (ListAbonnesResponse);
  rpc ListAbonnesActifs (EmptyRequest) returns (ListAbonnesResponse);
  rpc CreateAbonne (CreateAbonneRequest) returns (AbonneResponse);
  rpc UpdateAbonne (UpdateAbonneRequest) returns (AbonneResponse);
  rpc SuspendreAbonne (AbonneIdRequest) returns (AbonneResponse);
  rpc ReactiverAbonne (AbonneIdRequest) returns (AbonneResponse);
  rpc GetCompteur (AbonneIdRequest) returns (CompteurResponse);
  rpc RemplacerCompteur (RemplacerCompteurRequest) returns (CompteurResponse);
}

message AbonneIdRequest { string abonne_id = 1; }

message ListAbonnesRequest { string statut = 1; }

message CreateAbonneRequest {
  string nom = 1;
  string prenom = 2;
  string telephone_whatsapp = 3;
  string adresse = 4;
  int32 numero_compteur = 5;
  string quartier = 6;
  int32 camp = 7;
  double index_initial = 8;
  string date_pose = 9;
}

message UpdateAbonneRequest {
  string abonne_id = 1;
  string nom = 2;
  string prenom = 3;
  string telephone_whatsapp = 4;
  string adresse = 5;
}

message RemplacerCompteurRequest {
  string abonne_id = 1;
  double index_fermeture = 2;
  int32 nouveau_numero_compteur = 3;
  string nouveau_quartier = 4;
  int32 nouveau_camp = 5;
  double nouvel_index_initial = 6;
  string date_remplacement = 7;
}

message AbonneResponse {
  string abonne_id = 1;
  string numero_abonne = 2;
  string nom = 3;
  string prenom = 4;
  string telephone_whatsapp = 5;
  string adresse = 6;
  string statut = 7;
  CompteurResponse compteur = 8;
  string created_at = 9;
}

message CompteurResponse {
  string compteur_id = 1;
  int32 numero_compteur = 2;
  string quartier = 3;
  int32 camp = 4;
  double index_initial = 5;
  string date_pose = 6;
  string statut = 7;
}

message ListAbonnesResponse { repeated AbonneResponse abonnes = 1; }
message EmptyRequest {}
```

### 9.3 campagne_service.proto

```protobuf
syntax = "proto3";
package campagne;

service CampagneService {
  rpc CreateCampagne (CreateCampagneRequest) returns (CampagneResponse);
  rpc GetCampagne (CampagneIdRequest) returns (CampagneResponse);
  rpc ListCampagnes (EmptyRequest) returns (ListCampagnesResponse);
  rpc SaisirIndex (SaisirIndexRequest) returns (ReleveResponse);
  rpc MarquerNonReleve (MarquerNonReleveRequest) returns (ReleveResponse);
  rpc GetReleve (ReleveIdRequest) returns (ReleveResponse);
  rpc ListReleves (CampagneIdRequest) returns (ListRelevesResponse);
  rpc GetProgression (CampagneIdRequest) returns (ProgressionResponse);
  rpc CloturerCampagne (CampagneIdRequest) returns (CampagneResponse);
  rpc GetDernierIndex (AbonneIdRequest) returns (DernierIndexResponse);
}

message CreateCampagneRequest {
  string nom = 1;
  int32 periode_mois = 2;
  int32 periode_annee = 3;
  string date_planifiee = 4;
  string created_by = 5;
}

message SaisirIndexRequest {
  string campagne_id = 1;
  string abonne_id = 2;
  double nouveau_index = 3;
  string observation = 4;
  string agent_id = 5;
}

message MarquerNonReleveRequest {
  string campagne_id = 1;
  string abonne_id = 2;
  string statut = 3;  // NON_RELEVE ou ESTIME
  string observation = 4;
  string agent_id = 5;
}

message CampagneIdRequest { string campagne_id = 1; }
message ReleveIdRequest { string releve_id = 1; }
message AbonneIdRequest { string abonne_id = 1; }

message CampagneResponse {
  string campagne_id = 1;
  string nom = 2;
  int32 periode_mois = 3;
  int32 periode_annee = 4;
  string statut = 5;
  string date_planifiee = 6;
  string date_creation = 7;
  string date_cloture = 8;
}

message ReleveResponse {
  string releve_id = 1;
  string abonne_id = 2;
  double ancien_index = 3;
  double nouveau_index = 4;
  double consommation = 5;
  string date_releve = 6;
  string observation = 7;
  string statut = 8;
}

message ProgressionResponse {
  string campagne_id = 1;
  int32 total_abonnes = 2;
  int32 nb_releves = 3;
  int32 nb_en_attente = 4;
  double pourcentage = 5;
}

message DernierIndexResponse {
  string abonne_id = 1;
  double dernier_index = 2;
  bool est_index_initial = 3;
}

message ListCampagnesResponse { repeated CampagneResponse campagnes = 1; }
message ListRelevesResponse { repeated ReleveResponse releves = 1; }
message EmptyRequest {}
```

### 9.4 facturation_service.proto

```protobuf
syntax = "proto3";
package facturation;

service FacturationService {
  rpc GenererFactures (GenererFacturesRequest) returns (GenererFacturesResponse);
  rpc GetFacture (FactureIdRequest) returns (FactureResponse);
  rpc ListFactures (ListFacturesRequest) returns (ListFacturesResponse);
  rpc GetFacturePDF (FactureIdRequest) returns (PDFResponse);
  rpc UpdateStatutFacture (UpdateStatutRequest) returns (FactureResponse);
  rpc GetTarifActuel (EmptyRequest) returns (TarifResponse);
  rpc UpdateTarif (UpdateTarifRequest) returns (TarifResponse);
  rpc GetFacturesParCampagne (CampagneIdRequest) returns (ListFacturesResponse);
}

message GenererFacturesRequest { string campagne_id = 1; }

message FactureIdRequest { string facture_id = 1; }
message CampagneIdRequest { string campagne_id = 1; }

message ListFacturesRequest {
  string campagne_id = 1;
  string abonne_id = 2;
  string statut = 3;
}

message UpdateStatutRequest {
  string facture_id = 1;
  string statut = 2;
}

message UpdateTarifRequest {
  double prix_m3 = 1;
  string date_effet = 2;
}

message FactureResponse {
  string facture_id = 1;
  string numero_facture = 2;
  string abonne_id = 3;
  string campagne_id = 4;
  double ancien_index = 5;
  double nouveau_index = 6;
  double consommation = 7;
  double prix_m3 = 8;
  double montant = 9;
  string statut = 10;
  string date_releve = 11;
  string date_limite_paiement = 12;
  string date_generation = 13;
  string pdf_path = 14;
}

message TarifResponse {
  string tarif_id = 1;
  double prix_m3 = 2;
  string date_effet = 3;
  bool is_active = 4;
}

message PDFResponse {
  bytes pdf_content = 1;
  string filename = 2;
}

message GenererFacturesResponse { repeated FactureResponse factures = 1; }
message ListFacturesResponse { repeated FactureResponse factures = 1; }
message EmptyRequest {}
```

### 9.5 paiement_service.proto

```protobuf
syntax = "proto3";
package paiement;

service PaiementService {
  rpc EnregistrerPaiement (EnregistrerPaiementRequest) returns (PaiementResponse);
  rpc GetSolde (FactureIdRequest) returns (SoldeResponse);
  rpc ListPaiements (ListPaiementsRequest) returns (ListPaiementsResponse);
  rpc ListImpayes (EmptyRequest) returns (ListImpayesResponse);
  rpc GetSuiviImpaye (FactureIdRequest) returns (SuiviImpayeResponse);
}

message EnregistrerPaiementRequest {
  string facture_id = 1;
  string abonne_id = 2;
  double montant = 3;
  string date_paiement = 4;
  string mode_paiement = 5;
  string reference_transaction = 6;
  string enregistre_par = 7;
}

message FactureIdRequest { string facture_id = 1; }

message ListPaiementsRequest {
  string facture_id = 1;
  string abonne_id = 2;
}

message PaiementResponse {
  string paiement_id = 1;
  string facture_id = 2;
  double montant = 3;
  string date_paiement = 4;
  string mode_paiement = 5;
  string reference_transaction = 6;
  string created_at = 7;
}

message SoldeResponse {
  string facture_id = 1;
  double montant_total = 2;
  double montant_paye = 3;
  double solde_restant = 4;
  string statut = 5;
}

message SuiviImpayeResponse {
  string suivi_id = 1;
  string facture_id = 2;
  string abonne_id = 3;
  string date_depassement = 4;
  int32 etape_actuelle = 5;
  string resolu_le = 6;
}

message ListPaiementsResponse { repeated PaiementResponse paiements = 1; }
message ListImpayesResponse { repeated SoldeResponse impayes = 1; }
message EmptyRequest {}
```

### 9.6 notification_service.proto

```protobuf
syntax = "proto3";
package notification;

service NotificationService {
  rpc EnvoyerFacture (EnvoyerFactureRequest) returns (EnvoiResponse);
  rpc ReenvoyerFacture (FactureIdRequest) returns (EnvoiResponse);
  rpc EnvoyerRelance (EnvoyerRelanceRequest) returns (EnvoiResponse);
  rpc GetEnvoi (EnvoiIdRequest) returns (EnvoiResponse);
  rpc ListEnvois (ListEnvoisRequest) returns (ListEnvoisResponse);
  rpc ValiderToken (ValiderTokenRequest) returns (ValiderTokenResponse);
  rpc RevoquerToken (TokenIdRequest) returns (StatusResponse);
}

message EnvoyerFactureRequest {
  string facture_id = 1;
  string abonne_id = 2;
}

message EnvoyerRelanceRequest {
  string facture_id = 1;
  string abonne_id = 2;
  int32 etape = 3;
}

message FactureIdRequest { string facture_id = 1; }
message EnvoiIdRequest { string envoi_id = 1; }
message TokenIdRequest { string token_id = 1; }

message ListEnvoisRequest {
  string facture_id = 1;
  string abonne_id = 2;
}

message ValiderTokenRequest { string token = 1; }

message EnvoiResponse {
  string envoi_id = 1;
  string facture_id = 2;
  string statut = 3;
  string date_envoi = 4;
  string telnyx_message_id = 5;
  string erreur = 6;
}

message ValiderTokenResponse {
  bool is_valid = 1;
  string abonne_id = 2;
  string date_expiration = 3;
}

message ListEnvoisResponse { repeated EnvoiResponse envois = 1; }

message StatusResponse {
  bool success = 1;
  string message = 2;
}
```

### 9.7 reporting_service.proto

```protobuf
syntax = "proto3";
package reporting;

service ReportingService {
  rpc GetDashboard (EmptyRequest) returns (DashboardResponse);
  rpc GetStatsCampagne (CampagneIdRequest) returns (StatsCampagneResponse);
  rpc GetStatsGlobales (EmptyRequest) returns (StatsGlobalesResponse);
  rpc UpdateStatsCampagne (UpdateStatsCampagneRequest) returns (StatusResponse);
  rpc UpdateStatsFacturation (UpdateStatsFacturationRequest) returns (StatusResponse);
  rpc UpdateStatsPaiements (UpdateStatsPaiementsRequest) returns (StatusResponse);
}

message CampagneIdRequest { string campagne_id = 1; }

message DashboardResponse {
  StatsCampagneResponse campagne_en_cours = 1;
  StatsFacturationResponse facturation_en_cours = 2;
  StatsPaiementsResponse paiements_en_cours = 3;
}

message StatsCampagneResponse {
  string campagne_id = 1;
  string nom_campagne = 2;
  int32 total_abonnes = 3;
  int32 nb_releves = 4;
  int32 nb_en_attente = 5;
  double pourcentage_progression = 6;
  double consommation_totale = 7;
}

message StatsFacturationResponse {
  string campagne_id = 1;
  int32 total_factures = 2;
  double montant_total_facture = 3;
  int32 nb_factures_envoyees = 4;
  int32 nb_factures_payees = 5;
  int32 nb_impayes = 6;
}

message StatsPaiementsResponse {
  string campagne_id = 1;
  double montant_encaisse = 2;
  double montant_impaye = 3;
  int32 nb_impayes = 4;
  double taux_recouvrement = 5;
}

message StatsGlobalesResponse {
  repeated StatsCampagneResponse historique_campagnes = 1;
  double consommation_totale_globale = 2;
  double montant_total_facture_global = 3;
  double montant_total_encaisse_global = 4;
}

message UpdateStatsCampagneRequest {
  string campagne_id = 1;
  string nom_campagne = 2;
  int32 total_abonnes = 3;
  int32 nb_releves = 4;
  double consommation_totale = 5;
}

message UpdateStatsFacturationRequest {
  string campagne_id = 1;
  int32 delta_factures = 2;
  double delta_montant = 3;
  string type_update = 4;  // GENEREE, ENVOYEE, PAYEE
}

message UpdateStatsPaiementsRequest {
  string campagne_id = 1;
  double montant_paiement = 2;
  string type_update = 3;  // PAIEMENT, IMPAYE_RESOLU
}

message StatusResponse { bool success = 1; }
message EmptyRequest {}
```

### 9.8 config_service.proto

```protobuf
syntax = "proto3";
package config;

service ConfigService {
  rpc GetInfosSociete (EmptyRequest) returns (InfosSocieteResponse);
  rpc UpdateInfosSociete (UpdateInfosRequest) returns (InfosSocieteResponse);
  rpc GetConfig (ConfigKeyRequest) returns (ConfigResponse);
  rpc UpdateConfig (UpdateConfigRequest) returns (ConfigResponse);
  rpc ListConfigs (EmptyRequest) returns (ListConfigsResponse);
}

message EmptyRequest {}
message ConfigKeyRequest { string cle = 1; }

message UpdateInfosRequest {
  string nom = 1;
  string adresse = 2;
  string telephone = 3;
  string logo_path = 4;
}

message UpdateConfigRequest {
  string cle = 1;
  string valeur = 2;
}

message InfosSocieteResponse {
  string nom = 1;
  string adresse = 2;
  string telephone = 3;
  string logo_path = 4;
  string updated_at = 5;
}

message ConfigResponse {
  string cle = 1;
  string valeur = 2;
  string description = 3;
}

message ListConfigsResponse { repeated ConfigResponse configs = 1; }
```

---

## 10. Schéma GraphQL

```graphql
# ===================== TYPES =====================

type AuthPayload {
  accessToken: String!
  expiresIn: Int!
  user: User!
}
# Le refresh token n'est jamais exposé dans le corps de la réponse : la
# gateway le pose en cookie HttpOnly + Secure + SameSite=Strict (login,
# refreshToken) et le lit depuis ce cookie (refreshToken, logout), afin
# qu'il reste inaccessible à JS côté client (protection XSS).

type User {
  id: ID!
  username: String!
  email: String!        # Vide ("") pour les rôles non-ADMIN
  phoneNumber: String!  # Toujours présent (+237XXXXXXXXX)
  role: Role!
  isActive: Boolean!
  createdAt: String!
}

type OtpSentPayload {
  maskedPhone: String!  # Ex. "+237 6•• ••• •78" — dérivé de l'input
}

enum Role { ADMIN AGENT COMPTABLE SUPERVISEUR }

type Abonne {
  id: ID!
  numeroAbonne: String!
  nom: String!
  prenom: String!
  telephoneWhatsapp: String!
  adresse: String
  statut: StatutAbonne!
  compteur: Compteur!
  createdAt: String!
}

enum StatutAbonne { ACTIF SUSPENDU RESILIE }

type Compteur {
  id: ID!
  numeroCompteur: Int!
  quartier: String!
  camp: Int!
  indexInitial: Float!
  datePose: String!
  statut: StatutCompteur!
}

enum StatutCompteur { ACTIF REMPLACE DESACTIVE }

type Campagne {
  id: ID!
  nom: String!
  periodeMois: Int!
  periodeAnnee: Int!
  statut: StatutCampagne!
  datePlanifiee: String
  dateCreation: String!
  dateCloture: String
}

enum StatutCampagne { BROUILLON EN_COURS CLOTUREE }

type Releve {
  id: ID!
  campagneId: ID!
  abonneId: ID!
  ancienIndex: Float!
  nouvelIndex: Float
  consommation: Float
  dateReleve: String
  observation: String
  statut: StatutReleve!
}

enum StatutReleve { A_RELEVER RELEVE ESTIME NON_RELEVE }

type Progression {
  campagneId: ID!
  totalAbonnes: Int!
  nbReleves: Int!
  nbEnAttente: Int!
  pourcentage: Float!
}

type Tarif {
  id: ID!
  prixM3: Float!
  dateEffet: String!
  isActive: Boolean!
}

type Facture {
  id: ID!
  numeroFacture: String!
  abonneId: ID!
  campagneId: ID!
  ancienIndex: Float!
  nouvelIndex: Float!
  consommation: Float!
  prixM3: Float!
  montant: Float!
  statut: StatutFacture!
  dateReleve: String!
  dateLimitePaiement: String!
  dateGeneration: String!
  pdfPath: String
}

enum StatutFacture { GENEREE ENVOYEE PAYEE PARTIELLE IMPAYEE }

type Paiement {
  id: ID!
  factureId: ID!
  montant: Float!
  datePaiement: String!
  modePaiement: ModePaiement!
  referenceTransaction: String
  enregistrePar: ID!
  createdAt: String!
}

enum ModePaiement { ESPECES MOBILE_MONEY VIREMENT }

type Solde {
  factureId: ID!
  montantTotal: Float!
  montantPaye: Float!
  soldeRestant: Float!
  statut: StatutFacture!
}

type Envoi {
  id: ID!
  factureId: ID!
  typeEnvoi: String!
  statut: StatutEnvoi!
  dateEnvoi: String
  erreur: String
}

enum StatutEnvoi { EN_ATTENTE ENVOYE ECHEC }

type Dashboard {
  campagneEnCours: StatsCampagne
  facturationEnCours: StatsFacturation
  paiementsEnCours: StatsPaiements
}

type StatsCampagne {
  campagneId: ID!
  nomCampagne: String!
  totalAbonnes: Int!
  nbReleves: Int!
  nbEnAttente: Int!
  pourcentageProgression: Float!
  consommationTotale: Float!
}

type StatsFacturation {
  totalFactures: Int!
  montantTotalFacture: Float!
  nbFacturesEnvoyees: Int!
  nbFacturesPayees: Int!
  nbImpayes: Int!
}

type StatsPaiements {
  montantEncaisse: Float!
  montantImpaye: Float!
  nbImpayes: Int!
  tauxRecouvrement: Float!
}

type InfosSociete {
  nom: String!
  adresse: String
  telephone: String
  logoPath: String
}

type Config {
  cle: String!
  valeur: String!
  description: String
}

# ===================== INPUTS =====================

input CreateAbonneInput {
  nom: String!
  prenom: String!
  telephoneWhatsapp: String!
  adresse: String
  numeroCompteur: Int!
  quartier: String!
  camp: Int!
  indexInitial: Float!
  datePose: String!
}

input UpdateAbonneInput {
  nom: String
  prenom: String
  telephoneWhatsapp: String
  adresse: String
}

input CreateCampagneInput {
  nom: String!
  periodeMois: Int!
  periodeAnnee: Int!
  datePlanifiee: String
}

input SaisirIndexInput {
  campagneId: ID!
  abonneId: ID!
  nouvelIndex: Float!
  observation: String
}

input MarquerNonReleveInput {
  campagneId: ID!
  abonneId: ID!
  statut: StatutReleve!
  observation: String!
}

input PaiementInput {
  factureId: ID!
  montant: Float!
  datePaiement: String!
  modePaiement: ModePaiement!
  referenceTransaction: String
}

input InfosSocieteInput {
  nom: String!
  adresse: String
  telephone: String
}

# ===================== QUERIES =====================

type Query {
  # Auth
  me: User

  # Abonnés
  abonne(id: ID!): Abonne
  abonnes(statut: StatutAbonne): [Abonne!]!

  # Campagnes
  campagne(id: ID!): Campagne
  campagnes: [Campagne!]!
  releves(campagneId: ID!): [Releve!]!
  progression(campagneId: ID!): Progression

  # Facturation
  facture(id: ID!): Facture
  factures(campagneId: ID, statut: StatutFacture): [Facture!]!
  tarifActuel: Tarif

  # Paiements
  solde(factureId: ID!): Solde
  paiements(factureId: ID!): [Paiement!]!
  impayes: [Solde!]!

  # Notifications
  envois(factureId: ID): [Envoi!]!

  # Reporting
  dashboard: Dashboard
  statsCampagne(id: ID!): StatsCampagne
  statsGlobales: StatsGlobalesResponse

  # Configuration
  infosSociete: InfosSociete
  configs: [Config!]!
}

# ===================== MUTATIONS =====================

type Mutation {
  # Auth — connexion
  # identifier accepte un username ou un numéro de téléphone (+237XXXXXXXXX)
  login(identifier: String!, password: String!): AuthPayload!
  refreshToken: AuthPayload!  # lit le refresh token depuis le cookie HttpOnly, pas d'argument
  logout: Boolean!

  # Auth — gestion utilisateurs (ADMIN requis)
  createUser(username: String!, phoneNumber: String!, role: Role!, email: String): User!
  updateUser(id: ID!, email: String, role: Role, phoneNumber: String): User!
  deactivateUser(id: ID!): User!

  # Auth — activation et reset par e-mail (ADMIN uniquement, Brevo)
  requestPasswordReset(email: String!): Boolean!  # toujours true (ne révèle pas si l'e-mail existe)
  activateAccount(token: String!, password: String!): Boolean!
  resetPassword(token: String!, password: String!): Boolean!

  # Auth — activation et reset par OTP WhatsApp (tous les rôles)
  # Toujours true — ne révèle pas si le numéro est enregistré
  requestPhoneOtp(phoneNumber: String!): OtpSentPayload!
  verifyOtpAndSetPassword(phoneNumber: String!, otpCode: String!, password: String!): Boolean!

  # Abonnés
  createAbonne(input: CreateAbonneInput!): Abonne!
  updateAbonne(id: ID!, input: UpdateAbonneInput!): Abonne!
  suspendreAbonne(id: ID!): Abonne!
  reactiverAbonne(id: ID!): Abonne!
  remplacerCompteur(abonneId: ID!, input: RemplacerCompteurInput!): Compteur!

  # Campagnes
  createCampagne(input: CreateCampagneInput!): Campagne!
  saisirIndex(input: SaisirIndexInput!): Releve!
  marquerNonReleve(input: MarquerNonReleveInput!): Releve!
  cloturerCampagne(id: ID!): Campagne!

  # Facturation
  updateTarif(prixM3: Float!): Tarif!

  # Paiements
  enregistrerPaiement(input: PaiementInput!): Paiement!

  # Notifications
  envoyerFacture(factureId: ID!): Envoi!
  reenvoyerFacture(factureId: ID!): Envoi!

  # Configuration
  updateInfosSociete(input: InfosSocieteInput!): InfosSociete!
  updateConfig(cle: String!, valeur: String!): Config!
}
```

---

## 11. Concepts transversaux

### 11.1 Sécurité

**Authentification :**
- JWT (access token 24h, refresh token 7j) — le refresh token est posé en
  cookie HttpOnly + Secure + SameSite=Strict par la Gateway, jamais exposé
  au JS client (voir flux 6.1)
- Bcrypt pour les mots de passe (coût 12)
- Aucun compte n'est créé avec un mot de passe fixé par un admin :
  ADMIN → lien d'activation par e-mail (Brevo, 48h) ;
  Autres rôles → OTP à 6 chiffres par WhatsApp (10 min, haché en base).
  Même mécanisme pour la réinitialisation selon le rôle (voir flux 6.7)
- Blacklist des tokens révoqués dans Auth Service
- Blocage temporaire après 5 tentatives échouées (15 min)

**Autorisation :**
- Contrôle par rôle sur chaque resolver GraphQL
- Chaque service gRPC vérifie que l'appelant est le Gateway (mutual TLS en production)
- Tokens d'accès abonné : UUID v4, expiration configurable, révocables

**Transport :**
- TLS/HTTPS pour toutes les connexions externes (ngrok assure le HTTPS)
- gRPC utilise HTTP/2 en interne sur le réseau Kubernetes (ClusterIP)

**Frontend ↔ Gateway — toujours en same-origin, jamais en CORS :**
- Le cookie `refresh_token` est `SameSite=Strict` : un navigateur ne
  l'envoie **jamais** sur une requête cross-origin, même avec des headers
  CORS corrects. Configurer `CORS_ALLOWED_ORIGINS` ne résout donc pas le
  problème — il faut que le frontend et la Gateway soient vus comme la
  **même origine** par le navigateur.
- **En développement local :** Angular CLI doit proxyfier `/graphql` vers
  `http://localhost:8080` via `proxy.conf.json` (voir `CLAUDE.md`), plutôt
  que d'appeler la Gateway depuis une origine différente (`localhost:4200`
  → `localhost:8080` = deux origines distinctes pour le navigateur).
- **En production :** nginx (déjà en place devant la Gateway, voir §7) sert
  le build Angular **et** proxyfie `/graphql` sous le même domaine — exactement
  le même principe qu'en dev, à l'échelle de l'infra.
- Si une intégration tierce nécessite un jour un vrai cross-origin (ex. app
  mobile ou domaine séparé), il faudra explicitement passer le cookie en
  `SameSite=None` + `Secure=True` (HTTPS obligatoire) et activer
  `django-cors-headers` avec `CORS_ALLOW_CREDENTIALS=True` — non fait par
  défaut, car ça affaiblit la protection CSRF du cookie.

**Secrets :**
- Stockés dans des Kubernetes Secrets (base64 encodé)
- Jamais dans le code source ni dans les ConfigMaps
- Clé JWT, credentials PostgreSQL, clé API Telnyx

### 11.2 Logging

Chaque service produit des logs structurés en JSON :

```json
{
  "timestamp": "2026-06-01T08:23:45.123Z",
  "level": "INFO",
  "service": "campagne-service",
  "request_id": "uuid-v4",
  "user_id": "uuid",
  "action": "SaisirIndex",
  "abonne_id": "uuid",
  "campagne_id": "uuid",
  "message": "Index saisi avec succès",
  "duration_ms": 45
}
```

**Niveaux de log :**
- `DEBUG` : Développement uniquement
- `INFO` : Opérations normales
- `WARNING` : Situations anormales mais non bloquantes
- `ERROR` : Erreurs récupérables
- `CRITICAL` : Erreurs bloquantes, intervention requise

### 11.3 Gestion des erreurs

**Codes d'erreur gRPC utilisés :**
- `NOT_FOUND` : Ressource inexistante
- `INVALID_ARGUMENT` : Données invalides (ex: nouveau index < ancien)
- `PERMISSION_DENIED` : Accès refusé par rôle
- `UNAUTHENTICATED` : Token invalide ou expiré
- `ALREADY_EXISTS` : Doublon (ex: campagne même période)
- `INTERNAL` : Erreur serveur inattendue

**Transformation Gateway :**
Les erreurs gRPC sont converties en erreurs GraphQL avec un message clair en français.

### 11.4 Communication inter-services

Les services communiquent via gRPC appels synchrones. Pour les traitements asynchrones (génération de factures, relances), le service appelant émet un événement et retourne immédiatement, puis le service consommateur traite en arrière-plan.

**Pattern Event :**
Implémenté simplement via des appels gRPC asynchrones (sans broker de messages pour simplifier). Le Paiement Service appelle le Notification Service en gRPC de manière non bloquante pour les relances.

### 11.5 Cron Jobs

Deux cron jobs tournent en permanence :

| Job | Service | Heure | Action |
|---|---|---|---|
| `CampagnePlanifieeJob` | Campagne Service | 7h00 | Vérification J-1 et J des campagnes planifiées |
| `ImpayeCheckerJob` | Paiement Service | 8h00 | Vérification et déclenchement des relances impayées |

Implémentés avec `django-crontab` ou `APScheduler`.

### 11.6 Observabilité

L'observabilité repose sur 3 piliers complémentaires : **Logs**, **Métriques** et **Traces distribuées**. Sans ces 3 piliers, diagnostiquer un problème dans un système de 9 microservices est quasi impossible.

```
Observabilité
├── Logs      → Que s'est-il passé ?
├── Métriques → Comment le système se comporte-t-il ?
└── Traces    → Où est passée une requête entre les services ?
```

---

#### 11.6.1 Stack d'observabilité

```
┌─────────────────────────────────────────────────────┐
│                 STACK OBSERVABILITÉ                  │
│                                                      │
│  OpenTelemetry SDK  →  Instrumentation de chaque     │
│  (dans chaque service)    service Django + gRPC      │
│                                                      │
│  Prometheus   →  Collecte des métriques  →  Grafana  │
│  Loki         →  Agrégation des logs     →  Grafana  │
│  Jaeger       →  Traces distribuées      →  Jaeger UI│
└─────────────────────────────────────────────────────┘
```

| Outil | Rôle | Interface |
|---|---|---|
| **OpenTelemetry** | SDK d'instrumentation standard, injecté dans chaque service | — |
| **Prometheus** | Collecte et stockage des métriques (scraping toutes les 15s) | — |
| **Loki** | Agrégation centralisée des logs de tous les services | Grafana |
| **Jaeger** | Collecte et visualisation des traces distribuées | Jaeger UI |
| **Grafana** | Dashboard unifié Prometheus + Loki | Port 3000 |

---

#### 11.6.2 Pilier 1 — Logs centralisés (Loki)

Chaque service produit des logs JSON structurés (voir 11.2). Ces logs sont collectés et centralisés dans **Loki**, puis visualisés dans **Grafana**.

**Architecture de collecte :**

```
Service A logs JSON → stdout
Service B logs JSON → stdout      Promtail (agent)
Service C logs JSON → stdout  →   collecte tous les pods
        ...                   →   envoie vers Loki
                                       ↓
                                   Grafana (recherche, filtres)
```

**Requête Grafana/Loki — Exemple :**
```logql
# Tous les logs d'erreur du Facturation Service
{service="facturation-service"} |= "ERROR"

# Toutes les requêtes d'un abonné spécifique
{namespace="facturation-eau"} | json | abonne_id="uuid-xxx"

# Logs d'un TraceID spécifique (corrélation avec Jaeger)
{namespace="facturation-eau"} | json | trace_id="abc-123"
```

**Format de log enrichi avec OpenTelemetry :**
```json
{
  "timestamp": "2026-06-01T08:23:45.123Z",
  "level": "INFO",
  "service": "campagne-service",
  "trace_id": "abc123def456",
  "span_id": "xyz789",
  "request_id": "uuid-v4",
  "user_id": "uuid",
  "action": "SaisirIndex",
  "abonne_id": "uuid",
  "campagne_id": "uuid",
  "message": "Index saisi avec succès",
  "duration_ms": 45
}
```

> Le `trace_id` est la clé de corrélation entre logs, métriques et traces.

---

#### 11.6.3 Pilier 2 — Métriques (Prometheus + Grafana)

Chaque service Django expose un endpoint `/metrics` collecté par Prometheus.

**Métriques collectées automatiquement (OpenTelemetry) :**

| Métrique | Type | Description |
|---|---|---|
| `grpc_server_duration_ms` | Histogram | Latence des appels gRPC par méthode |
| `grpc_server_requests_total` | Counter | Nombre d'appels gRPC par service/méthode |
| `grpc_server_errors_total` | Counter | Nombre d'erreurs gRPC par code |
| `django_db_query_duration_ms` | Histogram | Latence des requêtes PostgreSQL |
| `process_memory_bytes` | Gauge | Mémoire consommée par pod |
| `process_cpu_seconds_total` | Counter | CPU consommé par pod |

**Métriques métier personnalisées :**

| Métrique | Service | Description |
|---|---|---|
| `factures_generees_total` | Facturation | Nombre de factures générées |
| `whatsapp_envoyes_total` | Notification | Nombre de messages envoyés |
| `whatsapp_echecs_total` | Notification | Nombre d'échecs d'envoi |
| `impayes_actifs` | Paiement | Nombre d'impayés en cours |
| `campagne_progression_percent` | Campagne | Progression de la campagne en cours |
| `paiements_enregistres_total` | Paiement | Nombre de paiements enregistrés |

**Dashboard Grafana — Tableau de bord opérationnel :**

```
┌─────────────────────────────────────────────────────┐
│              GRAFANA — Vue Opérationnelle             │
│                                                      │
│  Latence P95 API Gateway : 245ms    ✅               │
│  Taux d'erreur global    : 0.2%     ✅               │
│  Pods en bonne santé     : 9/9      ✅               │
│                                                      │
│  [Graphique latence gRPC par service - 24h]          │
│  [Graphique taux d'erreur par service - 24h]         │
│  [Graphique CPU/Mémoire par pod]                     │
│                                                      │
│  Alertes actives : 0                ✅               │
└─────────────────────────────────────────────────────┘

┌─────────────────────────────────────────────────────┐
│              GRAFANA — Vue Métier                    │
│                                                      │
│  Factures générées aujourd'hui : 48                  │
│  WhatsApp envoyés : 47 ✅  Échecs : 1 ⚠️            │
│  Impayés actifs   : 6                                │
│  Campagne en cours : 76% relevée                     │
│                                                      │
│  [Graphique consommation journalière]                │
│  [Graphique taux de recouvrement - 6 mois]           │
└─────────────────────────────────────────────────────┘
```

**Règles d'alerte Prometheus :**

```yaml
groups:
  - name: facturation-eau-alerts
    rules:
      - alert: ServiceDown
        expr: up{namespace="facturation-eau"} == 0
        for: 1m
        annotations:
          summary: "Service {{ $labels.service }} est DOWN"

      - alert: HighErrorRate
        expr: rate(grpc_server_errors_total[5m]) > 0.05
        for: 2m
        annotations:
          summary: "Taux d'erreur élevé sur {{ $labels.service }}"

      - alert: HighLatency
        expr: grpc_server_duration_ms{quantile="0.95"} > 2000
        for: 5m
        annotations:
          summary: "Latence élevée sur {{ $labels.service }}"

      - alert: WhatsAppFailures
        expr: rate(whatsapp_echecs_total[10m]) > 0
        for: 1m
        annotations:
          summary: "Échecs d'envoi WhatsApp détectés"
```

---

#### 11.6.4 Pilier 3 — Traces distribuées (OpenTelemetry + Jaeger)

Les traces distribuées permettent de suivre une requête à travers tous les services qu'elle traverse, avec le temps passé dans chacun.

**Fonctionnement :**

```
Requête Angular → API Gateway
│
│ TraceID: abc-123  SpanID: 001  [5ms]
│
├──► Auth Service (validation JWT)
│    TraceID: abc-123  SpanID: 002  [3ms]
│
├──► Campagne Service (SaisirIndex)
│    TraceID: abc-123  SpanID: 003  [45ms]
│    │
│    └──► PostgreSQL (INSERT releve)
│         TraceID: abc-123  SpanID: 004  [12ms]
│
└──► Reporting Service (UpdateStats)
     TraceID: abc-123  SpanID: 005  [8ms]

TOTAL: 73ms
```

**Vue Jaeger UI :**

```
Trace abc-123 — saisirIndex — 73ms total
│
├── api-gateway           5ms   ████
├── auth-service          3ms   ███
├── campagne-service     45ms   █████████████████████████████████████████
│   └── postgresql       12ms   ████████████
└── reporting-service     8ms   ███████
```

**Instrumentation OpenTelemetry dans Django :**

```python
# settings.py de chaque service
from opentelemetry import trace
from opentelemetry.sdk.trace import TracerProvider
from opentelemetry.exporter.jaeger.thrift import JaegerExporter
from opentelemetry.instrumentation.django import DjangoInstrumentor
from opentelemetry.instrumentation.grpc import GrpcInstrumentorServer

# Configuration automatique
DjangoInstrumentor().instrument()
GrpcInstrumentorServer().instrument()

# Export vers Jaeger
jaeger_exporter = JaegerExporter(
    agent_host_name="jaeger-service",
    agent_port=6831,
)
```

---

#### 11.6.5 Déploiement Kubernetes — Pods d'observabilité

```yaml
# Ajout dans le namespace facturation-eau

# Prometheus
- prometheus-deployment        (1 pod)
- prometheus-service           (ClusterIP 9090)
- prometheus-config            (ConfigMap)

# Loki + Promtail
- loki-deployment              (1 pod)
- loki-service                 (ClusterIP 3100)
- promtail-daemonset           (1 pod par nœud — collecte les logs)

# Jaeger
- jaeger-deployment            (1 pod — all-in-one pour dev)
- jaeger-service               (ClusterIP 6831/UDP + 16686/HTTP)

# Grafana
- grafana-deployment           (1 pod)
- grafana-service              (NodePort 3000)
- grafana-datasources          (ConfigMap — Prometheus + Loki)
- grafana-dashboards           (ConfigMap — dashboards pré-configurés)
```

**Accès depuis le MacBook :**

```bash
# Grafana (métriques + logs)
kubectl port-forward svc/grafana-service 3000:3000
→ http://localhost:3000

# Jaeger UI (traces)
kubectl port-forward svc/jaeger-service 16686:16686
→ http://localhost:16686

# Prometheus (métriques brutes)
kubectl port-forward svc/prometheus-service 9090:9090
→ http://localhost:9090
```

---

#### 11.6.6 Corrélation des 3 piliers — Cas pratique

**Scénario : Un admin signale que l'envoi WhatsApp a échoué à 14h32.**

```
Étape 1 — Grafana Métriques
  → Pic de whatsapp_echecs_total à 14h32
  → Service concerné : notification-service

Étape 2 — Grafana Logs (Loki)
  → Filtre : {service="notification-service"} |= "ERROR" | timestamp >= 14h32
  → Log trouvé : "Telnyx API timeout after 3 retries"
  → trace_id extrait : "def456ghi789"

Étape 3 — Jaeger Traces
  → Recherche par trace_id : def456ghi789
  → Trace complète : api-gateway → facturation-service → notification-service
  → notification-service : 15 secondes (timeout Telnyx)
  → Cause identifiée : latence réseau vers l'API Telnyx

Résolution : Ajuster le timeout Telnyx dans la configuration
             Vérifier la connectivité réseau du MacBook
```

---

## 12. Risques et dette technique

### 12.1 Risques techniques

| ID | Risque | Probabilité | Impact | Mitigation |
|---|---|---|---|---|
| R-001 | MacBook éteint = application inaccessible | Haute | Haute | Procédure de redémarrage documentée, ngrok en autostart |
| R-002 | URL ngrok change à chaque redémarrage (tier gratuit) | Haute | Moyenne | Upgrader ngrok ou fixer le domaine (ngrok payant) |
| R-003 | Blocage du compte Telnyx (abus) | Faible | Haute | Limiter les envois, monitor les échecs |
| R-004 | Corruption d'une base PostgreSQL | Faible | Haute | Sauvegardes automatiques quotidiennes |
| R-005 | Complexité gRPC + Kubernetes sur MacBook | Haute | Moyenne | Documentation détaillée, Docker Compose en fallback |

### 12.2 Dette technique identifiée

| ID | Dette | Priorité de remboursement |
|---|---|---|
| DT-001 | Pas de broker de messages (RabbitMQ/Kafka) — événements via gRPC direct | V2 — quand le volume augmentera |
| DT-002 | Pas de monitoring centralisé (Prometheus/Grafana) | V2 |
| DT-003 | Tests unitaires et d'intégration à écrire | En parallèle du développement |
| DT-004 | ngrok tier gratuit — URL instable | À remplacer dès mise en production réelle |
| DT-005 | Mutual TLS entre services non implémenté | V2 — sécurité renforcée |

---

## 13. Glossaire technique

| Terme | Définition |
|---|---|
| **gRPC** | Framework RPC haute performance utilisant Protocol Buffers et HTTP/2 |
| **Protocol Buffers (.proto)** | Format de sérialisation binaire définissant les contrats d'interface gRPC |
| **GraphQL** | Langage de requête pour API, permettant au client de spécifier exactement les données nécessaires |
| **Strawberry** | Librairie Python pour créer des APIs GraphQL avec Django |
| **Apollo Client** | Client GraphQL pour Angular |
| **JWT** | JSON Web Token — token signé contenant les informations d'identité et de rôle |
| **Kubernetes** | Système d'orchestration de conteneurs Docker |
| **Minikube** | Distribution Kubernetes mono-nœud pour développement local |
| **Canary Deployment** | Stratégie de déploiement progressif avec montée en charge contrôlée |
| **ClusterIP** | Type de service Kubernetes accessible uniquement au sein du cluster |
| **NodePort** | Type de service Kubernetes exposé sur un port du nœud hôte |
| **StatefulSet** | Ressource Kubernetes pour les applications avec état (bases de données) |
| **PersistentVolumeClaim** | Ressource Kubernetes pour le stockage persistant des données |
| **ngrok** | Outil créant un tunnel HTTPS sécurisé vers un serveur local |
| **PWA** | Progressive Web App — application web installable et fonctionnant hors connexion |
| **ReportLab** | Librairie Python de génération de fichiers PDF |
| **UUID v4** | Identifiant unique universel généré aléatoirement |
| **Read-only Aggregator** | Pattern de service qui consomme des événements pour maintenir une vue agrégée des données |
| **Cron Job** | Tâche planifiée s'exécutant à intervalles définis |
