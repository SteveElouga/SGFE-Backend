# CLAUDE.md — Contexte du projet pour Claude Code

## Vue d'ensemble

**Projet :** Système de Gestion de Facturation d'Eau  
**Architecture :** Microservices (9 composants)  
**Communication interne :** gRPC + Protocol Buffers v3  
**API externe :** GraphQL (Strawberry + Apollo Client)  
**Orchestration :** Kubernetes + Minikube (MacBook Pro)  
**Déploiement :** Canary Deployment  

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
│   ├── notification/           # Notification Service — WhatsApp Telnyx + tokens
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
├── frontend/                   # Angular PWA (mobile-first)
│
├── k8s/                        # Manifestes Kubernetes
│   ├── namespace.yaml
│   ├── services/               # 1 dossier par microservice
│   ├── databases/              # StatefulSets PostgreSQL
│   ├── observability/          # Prometheus, Loki, Jaeger, Grafana
│   └── secrets/                # Templates (sans valeurs réelles)
│
├── observability/              # Configuration observabilité
│   ├── prometheus/
│   ├── grafana/
│   ├── loki/
│   └── jaeger/
│
├── docs/                       # Documentation
│   ├── SRS.md                  # Spécification fonctionnelle (IEEE 830)
│   ├── ARCHITECTURE.md         # Documentation architecturale (C4 + Arc42)
│   └── ADR.md                  # Architecture Decision Records (26 ADRs)
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
PDF              ReportLab
WhatsApp         Telnyx API
Orchestration    Kubernetes + Minikube
Conteneurs       Docker
Frontend         Angular 18 + PWA
Observabilité    OpenTelemetry + Prometheus + Loki + Jaeger + Grafana
Déploiement      Canary Deployment
Serveur          MacBook Pro + ngrok
Auth             JWT (SimpleJWT) — access 24h, refresh 7j
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

AbonneCreated (Abonné → Campagne)
  Abonné Service notifie :
    → Campagne Service (ajoute à la campagne en cours si existante)
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

```bash
# Démarrer Minikube
minikube start --memory=8192 --cpus=4

# Appliquer tous les manifestes Kubernetes
kubectl apply -f k8s/

# Vérifier l'état des pods
kubectl get pods -n facturation-eau

# Tunnel ngrok vers l'API Gateway
ngrok http 8000

# Port-forward Grafana
kubectl port-forward svc/grafana-service 3000:3000 -n facturation-eau

# Port-forward Jaeger
kubectl port-forward svc/jaeger-service 16686:16686 -n facturation-eau
```

### Développement local (sans Kubernetes)

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

---

## Rôles et permissions

| Action | ADMIN | AGENT | COMPTABLE |
|---|---|---|---|
| Gérer les abonnés | ✅ | ❌ | ❌ |
| Créer/clôturer campagne | ✅ | ❌ | ❌ |
| Saisir un index | ✅ | ✅ | ❌ |
| Voir la progression | ✅ | ✅ | ❌ |
| Consulter les factures | ✅ | ❌ | ✅ |
| Enregistrer un paiement | ✅ | ❌ | ✅ |
| Envoyer WhatsApp | ✅ | ❌ | ✅ |
| Voir le dashboard | ✅ | ❌ | ✅ |
| Gérer les utilisateurs | ✅ | ❌ | ❌ |
| Modifier les paramètres | ✅ | ❌ | ❌ |

---

## Observabilité

```
Grafana      → http://localhost:3000  (métriques + logs)
Jaeger UI    → http://localhost:16686 (traces distribuées)
Prometheus   → http://localhost:9090  (métriques brutes)
```

Chaque service doit :
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

---

## Conventions de code

- **Langue des commentaires :** Français
- **Langue du code :** Anglais (noms de variables, fonctions, classes)
- **Type hints :** Obligatoires partout — jamais de `Any`
- **Docstrings :** Obligatoires sur toutes les fonctions publiques
- **Tests :** Chaque service doit avoir une couverture > 80%
- **Migrations :** Une migration par modification de modèle — jamais de squash en dev
- **Secrets :** Jamais dans le code — toujours dans les variables d'environnement
