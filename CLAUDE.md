# CLAUDE.md — Contexte du projet pour Claude Code

## Vue d'ensemble

**Projet :** Système de Gestion de Facturation d'Eau
**Architecture :** Microservices (9 composants)
**Communication interne :** gRPC + Protocol Buffers v3
**API externe :** GraphQL (Strawberry + Apollo Client)
**Orchestration :** Docker Compose — *Kubernetes reste une cible lointaine, pas un état*
**Déploiement :** Docker Compose sur EC2 ; canari côté frontend uniquement

---

## Règle fondamentale

> Chaque microservice est un projet Django indépendant avec sa propre base de données PostgreSQL.
> Les services ne communiquent JAMAIS directement avec la BD d'un autre service.
> Toute communication inter-services passe EXCLUSIVEMENT par gRPC.

---

## Structure du projet

```
facturation-eau/
│
├── gateway/                    # API Gateway — GraphQL (pas de BD)
├── services/
│   ├── auth/                   # Auth Service — JWT, rôles
│   ├── abonne/                 # Abonné Service — abonnés + compteurs
│   ├── campagne/               # Campagne Service — campagnes + relevés
│   ├── facturation/            # Facturation Service — factures + PDF + tarifs
│   ├── paiement/               # Paiement Service — paiements + impayés
│   ├── notification/           # Notification Service — WhatsApp (whatsapp-web.js) + tokens
│   ├── reporting/              # Reporting Service — agrégateur read-only
│   └── config/                 # Config Service — paramètres système
│
├── proto/                      # Fichiers .proto partagés entre tous les services
│   ├── auth_service.proto
│   ├── abonne_service.proto
│   ├── campagne_service.proto
│   ├── facturation_service.proto
│   ├── paiement_service.proto
│   ├── notification_service.proto
│   ├── reporting_service.proto
│   └── config_service.proto
│
├── nginx/                      # Seul point d'entrée publié (:8080)
├── whatsapp-service/           # Node.js + Chromium (whatsapp-web.js)
├── scripts/                    # gen-jwt-keys.sh, backup-databases.sh
│
├── docker-compose.yml          # 21 services — le mode de démarrage réel
├── docker-compose.prod.yml     # Surcouche de durcissement
│
├── docs/                       # Documentation
│   ├── SRS.md                  # Spécification fonctionnelle (IEEE 830)
│   ├── ARCHITECTURE.md         # Documentation architecturale (C4 + Arc42)
│   ├── ADR.md                  # Architecture Decision Records (28 ADRs)
│   ├── WORKFLOWS.md            # Ce que fait réellement le code, pas à pas
│   ├── ETAT_DU_SYSTEME.md      # Registre des anomalies (ANO-XXX)
│   ├── INFRASTRUCTURE_AWS.md   # Dimensionnement, coûts, services managés
│   └── CHAINE_DE_LIVRAISON.md  # Déploiement automatisé : qui fait quoi
│
├── CLAUDE.md                   # Ce fichier
├── .cursorrules
├── .cursorignore
└── .env.example
```

---

## Les 9 composants — Référence rapide

| # | Service | Dossier | Port gRPC | BD | Rôle |
|---|---|---|---|---|---|
| 0 | API Gateway | `gateway/` | N/A | ❌ | GraphQL → gRPC |
| 1 | Auth Service | `services/auth/` | 50051 | auth_db | JWT, rôles |
| 2 | Abonné Service | `services/abonne/` | 50052 | abonne_db | Abonnés + compteurs |
| 3 | Campagne Service | `services/campagne/` | 50053 | campagne_db | Campagnes + relevés |
| 4 | Facturation Service | `services/facturation/` | 50054 | facturation_db | Factures + PDF + tarifs |
| 5 | Paiement Service | `services/paiement/` | 50055 | paiement_db | Paiements + impayés |
| 6 | Notification Service | `services/notification/` | 50056 | notification_db | WhatsApp + tokens |
| 7 | Reporting Service | `services/reporting/` | 50057 | reporting_db | Dashboard read-only |
| 8 | Config Service | `services/config/` | 50058 | config_db | Paramètres système |

---

## Stack technologique complète

```
Backend          Django 5.x + Django REST Framework
gRPC             grpcio + grpcio-tools + grpc-stubs
GraphQL          Strawberry (gateway) + Apollo Client (frontend)
Base de données  PostgreSQL 16 (1 instance par service)
PDF              WeasyPrint 69
WhatsApp         whatsapp-web.js (service Node.js auto-hébergé, compte dédié, zéro coût)
E-mail           Brevo API (activation de compte, réinitialisation de mot de passe — 300/jour gratuits)
Orchestration    Docker Compose (21 services)
Conteneurs       Docker — 12 Dockerfiles, images de base épinglées au SHA
Frontend         Angular 22 + PrimeNG 21 + PWA
Observabilité    ⚠️ AUCUNE aujourd'hui — dépendances présentes, 0 fichier instrumenté
Déploiement      Compose ; cible AWS — voir docs/INFRASTRUCTURE_AWS.md
Serveur          local ; cible EC2 t4g.medium en eu-west-3
Auth             JWT (SimpleJWT) — access 15 min par défaut (cookie HttpOnly pour le refresh, 7j)
Scheduler        APScheduler (cron jobs)
```

---

## Structure interne de chaque service Django

```
services/[nom_service]/
│
├── [nom_service]/              # Application Django principale
│   ├── settings.py
│   ├── urls.py
│   └── wsgi.py
│
├── [domaine]/                  # App Django du domaine métier
│   ├── models.py               # Modèles PostgreSQL
│   ├── migrations/
│   ├── grpc_server.py          # Implémentation du servicer gRPC
│   ├── grpc_clients.py         # Clients gRPC vers autres services
│   ├── services.py             # Logique métier (domain layer)
│   ├── repositories.py         # Accès base de données
│   ├── serializers.py          # Sérialisation des données
│   └── tests/
│       ├── test_models.py
│       ├── test_services.py
│       └── test_grpc.py
│
├── proto/                      # Stubs générés depuis les .proto
│   ├── [nom]_pb2.py            # (généré — NE PAS MODIFIER)
│   └── [nom]_pb2_grpc.py       # (généré — NE PAS MODIFIER)
│
├── Dockerfile
├── requirements.txt
├── manage.py
├── CLAUDE.md                   # Contexte spécifique à ce service
└── .env.example
```

---

## Fichiers .proto — Source de vérité

Les fichiers `.proto` sont dans `proto/` à la racine du projet.
Après toute modification d'un `.proto`, régénérer les stubs :

```bash
python -m grpc_tools.protoc \
  -I proto/ \
  --python_out=services/[nom]/proto/ \
  --grpc_python_out=services/[nom]/proto/ \
  proto/[nom]_service.proto
```

**NE JAMAIS modifier les fichiers `*_pb2.py` et `*_pb2_grpc.py` — ils sont générés automatiquement.**

---

## Règles métier critiques

```python
# Calcul de consommation
consommation = nouveau_index - ancien_index  # toujours >= 0

# Calcul du montant d'une facture
montant = consommation * prix_m3  # prix_m3 copié depuis le tarif actif

# Calcul du solde restant
solde_restant = montant_total - sum(versements)

# Date limite de paiement
date_limite = date_releve + timedelta(days=config.delai_paiement_jours)  # défaut: 5

# Expiration token abonné
date_expiration = date_envoi + timedelta(days=config.token_validite_jours)  # défaut: 20

# Statut facture
if montant_paye == 0:          statut = 'IMPAYEE'
elif montant_paye < montant:   statut = 'PARTIELLE'
else:                          statut = 'PAYEE'
```

---

## Validations obligatoires

```python
# TOUJOURS valider avant de sauvegarder un relevé
assert nouveau_index >= ancien_index, "Le nouvel index ne peut pas être inférieur à l'ancien"

# TOUJOURS vérifier le statut de l'abonné avant ajout en campagne
assert abonne.statut == 'ACTIF', "Un abonné suspendu ne peut pas être relevé"

# TOUJOURS copier le prix_m3 dans la facture (pas de référence au tarif)
facture.prix_m3 = tarif_actif.prix_m3  # copie, jamais FK

# TOUJOURS vérifier le rôle avant toute action sensible
assert user.role in roles_autorises, "Accès non autorisé"

# Référence transaction obligatoire pour Mobile Money et Virement
if mode in ('MOBILE_MONEY', 'VIREMENT'):
    assert reference_transaction, "Référence de transaction obligatoire"
```

---

## Communication inter-services — Pattern gRPC

```python
# Exemple : Abonné Service appelé depuis Facturation Service

import grpc
from proto import abonne_service_pb2, abonne_service_pb2_grpc

def get_abonne(abonne_id: str):
    with grpc.insecure_channel('abonne-service:50052') as channel:
        stub = abonne_service_pb2_grpc.AbonneServiceStub(channel)
        response = stub.GetAbonne(
            abonne_service_pb2.AbonneIdRequest(abonne_id=abonne_id)
        )
        return response
```

**Adresses des services (Kubernetes ClusterIP) :**

```python
GRPC_SERVICES = {
    'auth':          'auth-service:50051',
    'abonne':        'abonne-service:50052',
    'campagne':      'campagne-service:50053',
    'facturation':   'facturation-service:50054',
    'paiement':      'paiement-service:50055',
    'notification':  'notification-service:50056',
    'reporting':     'reporting-service:50057',
    'config':        'config-service:50058',
}
```

---

## Événements inter-services — Qui appelle qui

```
CampagneCloturee (Campagne → Facturation)
  Campagne Service émet → Facturation Service génère les factures

FactureGeneree (Facturation → Paiement + Notification + Reporting)
  Facturation Service notifie :
    → Paiement Service (crée le SoldeFacture initial)
    → Notification Service (peut déclencher envoi WhatsApp)
    → Reporting Service (met à jour les stats)

PaiementEnregistre (Paiement → Facturation + Reporting)
  Paiement Service notifie :
    → Facturation Service (met à jour le statut facture)
    → Reporting Service (met à jour les stats paiements)

SuspensionRequise (Paiement → Abonné + Notification)
  Paiement Service (cron impayés) notifie :
    → Abonné Service (suspend l'abonné)
    → Notification Service (envoie WhatsApp étape 4)

Vérification du statut abonné (Campagne → Abonné)
  Au moment de la saisie d'un index (ajout d'un abonné à une campagne),
  Campagne Service interroge synchroniquement Abonné Service
  (GetAbonne) pour vérifier que l'abonné est ACTIF avant de créer le
  relevé — appel gRPC à la demande, pas un événement poussé par Abonné
  Service. Abonné Service publie par ailleurs ABONNE_CREATED/ABONNE_UPDATED
  sur Redis pub/sub, mais ce canal n'est consommé que par la Gateway
  (subscriptions GraphQL temps réel), jamais par Campagne Service.
```

---

## Cron Jobs

```python
# services/campagne/domaine/schedulers.py
# S'exécute à 7h00 chaque matin
def campagne_planifiee_job():
    """Vérifie les campagnes planifiées pour J-1 et J."""

# services/paiement/domaine/schedulers.py
# S'exécute à 8h00 chaque matin
def impaye_checker_job():
    """Vérifie et déclenche les relances impayées."""
```

---

## Commandes utiles

### Démarrage de l'environnement

> ⚠️ **Corrigé le 28 août 2026.** Ce bloc donnait des commandes Minikube et
> `kubectl` pour des manifestes `k8s/` et un dossier `observability/` qui
> **n'existent pas dans le dépôt**. Le seul mode de démarrage réel est
> Docker Compose.

```bash
# Tout le stack — 21 services
docker compose up -d --build

# État et santé des conteneurs (les 11 Dockerfiles portent un HEALTHCHECK)
docker compose ps --format json

# Logs d'un service
docker compose logs -f gateway --tail=50

# Point d'entrée : seul nginx est publié — HTTPS uniquement, :80 redirige
# vers :443 sans préserver de port personnalisé dans la redirection, donc
# ouvrir directement le port HTTPS publié (certificat auto-signé de dev à
# générer une fois, voir ci-dessous — le navigateur avertira, c'est normal)
./scripts/generate-nginx-cert.sh   # une seule fois, avant le premier up
open https://localhost:8443/graphql
```

Pour la production sur AWS, voir `docs/INFRASTRUCTURE_AWS.md` (dimensionnement,
coûts) et `docs/CHAINE_DE_LIVRAISON.md` (qui déploie quoi).

### Développement local, service par service

```bash
# Démarrer un service individuellement
cd services/campagne
python manage.py runserver 800X

# Démarrer le serveur gRPC d'un service
python manage.py grpc_server

# Générer les stubs depuis les .proto
make proto-gen SERVICE=campagne

# Lancer les migrations
python manage.py migrate

# Lancer les tests
python manage.py test
```

### Docker

```bash
# Build d'un service
docker build -t facturation-eau/campagne:latest services/campagne/

# Build de tous les services
make build-all

# Démarrer en local avec Docker Compose (alternative à Kubernetes)
docker-compose up
```

### Frontend (Angular) — proxy vers la Gateway, jamais de CORS

Le cookie `refresh_token` (HttpOnly, `SameSite=Strict`) n'est envoyé par le
navigateur que si le frontend et la Gateway sont vus comme la **même
origine**. Ne JAMAIS résoudre une erreur CORS ici en ouvrant
`CORS_ALLOWED_ORIGINS`/`SameSite=None` côté Gateway — ça ne résoudrait pas
le cookie de toute façon, et ça affaiblirait sa protection CSRF pour rien
(voir `docs/ARCHITECTURE.md` §11.1).

En développement local, faire proxyfier `/graphql` par le serveur de dev
Angular plutôt que d'appeler `https://localhost:8443` directement depuis
`http://localhost:4200` :

```jsonc
// frontend/proxy.conf.json
{
  "/graphql": {
    "target": "https://localhost:8443",
    "secure": false,
    "changeOrigin": true,
    "ws": true
  }
}
```

```jsonc
// frontend/angular.json — dans serve.options
"options": {
  "proxyConfig": "proxy.conf.json"
}
```

```bash
ng serve --proxy-config proxy.conf.json
```

Le client GraphQL (Apollo) doit appeler `/graphql` en **chemin relatif**
(pas `https://localhost:8443/graphql`) et envoyer les credentials :

```ts
new HttpLink({ uri: '/graphql', withCredentials: true })
```

> ⚠️ **Corrigé le 3 septembre 2026.** Cette section ciblait encore
> `http://localhost:8080` après le durcissement TLS de nginx (voir
> `docs/RUNBOOK.md` §2.1.d) : `location / { return 301
> https://$host$request_uri; }` redirige tout `:80` vers `:443` **sans
> port explicite** dans `$host` — donc ni un navigateur ni le proxy du
> serveur de dev Angular n'atteignaient plus la Gateway via `8080`. Cible
> désormais le port HTTPS réellement publié (`8443`), avec le certificat
> auto-signé de dev (`./scripts/generate-nginx-cert.sh`, prérequis avant
> le premier `docker compose up` — voir plus haut).

> ⚠️ **Corrigé le 31 août 2026.** Ce paragraphe affirmait que le nginx de ce
> dépôt « sert le build Angular ». Il ne l'a jamais servi : `nginx/default.conf`
> n'a ni `root` ni `try_files`, une seule `location /` qui proxyfie tout vers
> `gateway:8000`, et un CSP `default-src 'none'` qui bloquerait le moindre
> script. Quatrième prescription de ce fichier que le code n'a jamais suivie,
> après les trois corrigées le 28 août.

En production, ce rôle est tenu par le **nginx du dépôt frontend**
(`SGFE-frontend/nginx/conf.d/default.conf`) : c'est lui qui sert le build
Angular, et lui qui proxyfie sous le même domaine `/graphql` (y compris le
WebSocket des subscriptions), l'espace abonné, les PDF de factures, les CSV et
le bilan des impayés.

Les deux piles sont deux projets Compose distincts, donc deux réseaux étanches.
La jointure est le réseau **`sgfe-edge`**, créé par ce dépôt et rejoint par le
frontend en `external: true` ; **seul** le service `gateway` y adhère côté
backend. Le nginx de ce dépôt reste l'arête de l'API pour un accès direct
(publié en `8080:80`) — il n'est pas sur le chemin du navigateur.

Conséquence sur l'ordre de démarrage : **cette pile d'abord**. Le `docker
compose up` du frontend échoue en disant que le réseau n'existe pas — un échec
lisible, préférable à un nginx qui démarre et sert des 502.

---

## Rôles et permissions

`ADMIN` est le super-utilisateur : accès total, sans restriction, à tout le système.

| Action | ADMIN | AGENT | COMPTABLE | SUPERVISEUR |
|---|---|---|---|---|
| Gérer les abonnés | ✅ | ❌ | ❌ | ❌ |
| Créer/clôturer campagne | ✅ | ❌ | ❌ | ✅ (les siennes uniquement) |
| Saisir un index | ✅ | ✅ | ❌ | ✅ (sur ses propres campagnes) |
| Voir la progression | ✅ | ✅ | ❌ | ✅ (sur ses propres campagnes) |
| Consulter les factures | ✅ | ❌ | ✅ | ❌ |
| Enregistrer un paiement | ✅ | ❌ | ✅ | ❌ |
| Envoyer WhatsApp | ✅ | ❌ | ✅ | ❌ |
| Voir le dashboard | ✅ | ❌ | ✅ | ❌ |
| Gérer les utilisateurs | ✅ | ❌ | ❌ | ❌ |
| Modifier les paramètres | ✅ | ❌ | ❌ | ❌ |

`SUPERVISEUR` ne voit/gère jamais les campagnes créées par un autre
utilisateur (filtrage par `campagne.created_by`) — implémenté côté
Gateway (`gateway/schema/campagne_queries.py`, `_verifier_acces_campagne`)
et relayé via le paramètre `created_by` de `ListCampagnesRequest` côté
`campagne-service` (`campagnes/repositories.py::list_all`).

---

## Observabilité

> ⚠️ **Corrigé le 28 août 2026.** Cette section décrivait Grafana, Jaeger et
> Prometheus comme s'ils tournaient. **Rien n'est instrumenté** : les huit
> services portent bien `opentelemetry` et `prometheus_client` dans leurs
> `requirements.txt`, mais **zéro fichier** initialise un `TracerProvider` ou
> expose `/metrics`, et le dossier `observability/` n'existe pas. Un incident
> en production se diagnostique aujourd'hui par `docker compose logs`.

Ce qui reste à faire (points 58 à 60 du registre), chaque service devant :
1. Produire des logs JSON structurés avec `trace_id`
2. Exposer `/metrics` pour Prometheus
3. Être instrumenté avec OpenTelemetry SDK

---

## Documentation complète

| Document | Chemin | Contenu |
|---|---|---|
| SRS | `docs/SRS.md` | Exigences fonctionnelles, User Stories, Règles métier |
| Architecture | `docs/ARCHITECTURE.md` | C4 Model, flux, modèles de données, .proto, GraphQL |
| ADR | `docs/ADR.md` | 26 décisions architecturales documentées |
| Conformité CI/CD | `docs/CONFORMITE_CICD.md` | OWASP CI/CD Top 10, SLSA, NIST SSDF, CIS, durcissement GitHub Actions — preuve `fichier:ligne` par critère |
| Conformité OWASP/SOC 2 | `docs/CONFORMITE_SOC2_OWASP.md` | Diagnostic de préparation (pas une certification) — OWASP Top 10/API/ASVS, SOC 2 CC1-CC9 |

---

## i18n — messages utilisateur

Les messages destinés à un humain (erreurs de validation métier, messages
d'exception remontés jusqu'à une réponse gRPC/GraphQL) sont externalisés via
`django.utils.translation.gettext_lazy` (importé `as _`) dans chacun des 8
services — `services.py`, `grpc_server.py`, `grpc_interceptors.py`,
`repositories.py`, `validators.py`, `throttle.py`, `models.py` selon le
service, partout où un tel littéral existe. **Aujourd'hui, ceci externalise
sans traduire** : le texte reste français, identique caractère pour
caractère, tant qu'aucun catalogue `.po` n'est activé. Ne sont **jamais**
enveloppés : les messages de logs techniques (`logger.warning/error/
exception`), les docstrings, les commentaires, ni les identifiants
techniques déjà structurés (codes d'action d'audit, valeurs d'énumération).

`_("texte {var}").format(var=valeur)` reste valide (l'objet lazy retourné
par `_()` supporte `.format()`, y compris les conversions `!r`/`!s`) — seul
piège vérifié : ne jamais nommer une variable locale `_` (ex.
`total, _, _ = fn()`) dans une fonction qui appelle aussi `_(...)`, sous
peine de masquer l'alias gettext pour toute la fonction.

`LANGUAGE_CODE = "fr-fr"`, `USE_I18N = True` et `LOCALE_PATHS = [BASE_DIR /
"locale"]` sont posés dans le `settings.py` de chaque service ; le dossier
`locale/` à la racine de chaque service existe déjà (vide) et est prêt à
recevoir un futur catalogue. Pour générer et compiler une traduction plus
tard (**non fait, hors périmètre pour l'instant**), depuis la racine d'un
service :

```bash
cd services/<nom>
python manage.py makemessages -l en   # génère locale/en/LC_MESSAGES/django.po
python manage.py compilemessages       # compile les .po en .mo
```

---

## Conventions de code

- **Langue des commentaires :** Français
- **Langue du code :** Anglais (noms de variables, fonctions, classes)
- **Type hints :** Obligatoires partout — jamais de `Any`
- **Docstrings :** Obligatoires sur toutes les fonctions publiques
- **Tests :** Chaque service doit avoir une couverture > 80%
- **Migrations :** Une migration par modification de modèle — jamais de squash en dev
- **Secrets :** Jamais dans le code — toujours dans les variables d'environnement
