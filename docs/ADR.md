# ADR — Architecture Decision Records
## Système de Gestion de Facturation d'Eau

> **Format de référence :** Architecture Decision Records (Michael Nygard)  
> **Version :** 1.0.0  
> **Date :** Juin 2026  
> **Statut global :** Validé  

---

## Table des matières

| ADR | Titre | Statut |
|---|---|---|
| [ADR-001](#adr-001) | Architecture microservices | ✅ Accepté |
| [ADR-002](#adr-002) | gRPC pour la communication inter-services | ✅ Accepté |
| [ADR-003](#adr-003) | GraphQL comme interface API externe unique | ✅ Accepté |
| [ADR-004](#adr-004) | Django pour tous les services backend | ✅ Accepté |
| [ADR-005](#adr-005) | PostgreSQL par service | ✅ Accepté |
| [ADR-006](#adr-006) | Kubernetes + Minikube pour l'orchestration | ✅ Accepté |
| [ADR-007](#adr-007) | Canary Deployment comme stratégie de déploiement | ✅ Accepté |
| [ADR-008](#adr-008) | Angular PWA mobile-first | ✅ Accepté |
| [ADR-009](#adr-009) | MacBook Pro + ngrok comme serveur | ✅ Accepté |
| [ADR-010](#adr-010) | Telnyx comme fournisseur WhatsApp | ✅ Accepté |
| [ADR-011](#adr-011) | ReportLab pour la génération de PDF | ✅ Accepté |
| [ADR-012](#adr-012) | Lien tokenisé pour l'espace abonné | ✅ Accepté |
| [ADR-013](#adr-013) | Rejet de SQLite en faveur de PostgreSQL | ✅ Accepté |
| [ADR-014](#adr-014) | Rejet du monolithe en faveur des microservices | ✅ Accepté |
| [ADR-015](#adr-015) | Rejet de REST en faveur de GraphQL | ✅ Accepté |
| [ADR-016](#adr-016) | whatsapp-web.js comme alternative gratuite | 🔄 En évaluation |
| [ADR-017](#adr-017) | Strawberry comme librairie GraphQL Python | ✅ Accepté |
| [ADR-018](#adr-018) | API Gateway sans base de données | ✅ Accepté |
| [ADR-019](#adr-019) | Reporting Service en read-only aggregator | ✅ Accepté |
| [ADR-020](#adr-020) | Fusion Compteurs dans Abonné Service | ✅ Accepté |
| [ADR-021](#adr-021) | Fusion Tarification dans Facturation Service | ✅ Accepté |
| [ADR-022](#adr-022) | Renommage WhatsApp Service en Notification Service | ✅ Accepté |
| [ADR-023](#adr-023) | Prix du m³ copié dans la facture à la génération | ✅ Accepté |
| [ADR-024](#adr-024) | Délai de paiement de 5 jours | ✅ Accepté |
| [ADR-025](#adr-025) | Processus de relance impayés en 4 étapes | ✅ Accepté |
| [ADR-026](#adr-026) | Stack d'observabilité : OpenTelemetry + Prometheus + Loki + Jaeger + Grafana | ✅ Accepté |

---

## ADR-001

### Titre : Architecture microservices

**Date :** Juin 2026  
**Statut :** ✅ Accepté  
**Décideurs :** Équipe projet  

---

#### Contexte

Le système doit gérer un cycle métier complet (abonnés → relevés → factures → paiements → notifications). La question se pose de savoir si l'application doit être construite comme un seul bloc (monolithe) ou découpée en services indépendants (microservices).

#### Décision

Adopter une **architecture microservices** avec 9 composants indépendants (1 API Gateway + 8 services métier), chacun avec sa propre base de données PostgreSQL.

#### Raisons

- **Objectif CV :** Démontrer la maîtrise des architectures distribuées, très recherchées sur le marché.
- **Isolation des pannes :** La panne d'un service (ex: Notification) n'affecte pas les autres (Facturation, Paiements).
- **Déployabilité indépendante :** Chaque service peut être mis à jour sans redéployer l'ensemble.
- **Séparation des responsabilités :** Chaque service possède un périmètre métier clairement défini.
- **Scalabilité future :** Même si le volume est faible aujourd'hui (50 abonnés), la structure supporte la croissance.

#### Compromis acceptés

- Complexité opérationnelle significativement plus élevée qu'un monolithe.
- Communication inter-services via gRPC à implémenter et maintenir.
- 8 bases de données PostgreSQL à gérer simultanément.
- Transactions distribuées plus complexes (pas de jointures cross-service).
- Courbe d'apprentissage plus longue pour un développeur seul.

#### Alternatives considérées

| Alternative | Raison du rejet |
|---|---|
| Monolithe Django unique | Insuffisant pour un projet CV ambitieux, moins formateur |
| Monolithe modulaire | Compromis intéressant mais choix délibéré de microservices |
| Microservices + broker Kafka | Trop complexe pour une première version, prévu en V2 |

---

## ADR-002

### Titre : gRPC pour la communication inter-services

**Date :** Juin 2026  
**Statut :** ✅ Accepté  
**Décideurs :** Équipe projet  

---

#### Contexte

Les microservices doivent communiquer entre eux. Plusieurs protocoles sont possibles : REST/HTTP, gRPC, ou messagerie asynchrone (RabbitMQ, Kafka).

#### Décision

Utiliser **gRPC avec Protocol Buffers v3** pour toute communication inter-services synchrone.

#### Raisons

- **Contrats forts :** Les fichiers `.proto` définissent des interfaces strictement typées, réduisant les erreurs d'intégration.
- **Performance :** gRPC utilise HTTP/2 et la sérialisation binaire (Protocol Buffers), bien plus performant que JSON/REST.
- **Génération de code automatique :** Les stubs client/serveur sont générés depuis les `.proto`, réduisant le code boilerplate.
- **Streaming :** gRPC supporte le streaming bidirectionnel (utile pour la progression des campagnes en temps réel).
- **Interopérabilité :** Standard industriel utilisé par Google, Netflix, Uber.
- **Valeur CV :** La maîtrise de gRPC est un vrai différenciateur technique.

#### Compromis acceptés

- Courbe d'apprentissage plus élevée que REST.
- Débogage plus complexe (binaire vs JSON lisible).
- Nécessite la gestion des fichiers `.proto` et la régénération des stubs à chaque modification.
- Moins accessible pour les outils de test simples (pas de curl direct).

#### Alternatives considérées

| Alternative | Raison du rejet |
|---|---|
| REST/HTTP entre services | Moins performant, pas de contrats forts, over-fetching possible |
| RabbitMQ / Kafka | Trop complexe pour la V1, communication asynchrone non nécessaire partout |
| GraphQL entre services | Inadapté à la communication interne, GraphQL est prévu pour l'interface externe |

---

## ADR-003

### Titre : GraphQL comme interface API externe unique

**Date :** Juin 2026  
**Statut :** ✅ Accepté  
**Décideurs :** Équipe projet  

---

#### Contexte

L'API Gateway doit exposer une interface pour le frontend Angular. Le choix porte entre REST traditionnel et GraphQL.

#### Décision

Exposer une **API GraphQL unique** via l'API Gateway, utilisant Strawberry (Python) côté backend et Apollo Client côté Angular.

#### Raisons

- **Endpoint unique :** Un seul point d'entrée `/graphql` au lieu de dizaines d'endpoints REST.
- **Pas de sur-fetch :** Le client Angular demande exactement les champs dont il a besoin (critique pour les agents mobiles sur réseau lent).
- **Pas de sous-fetch :** Une seule requête peut récupérer des données de plusieurs services agrégées.
- **Schéma auto-documenté :** L'introspection GraphQL permet à Angular de connaître tous les types disponibles.
- **Versionless :** L'API évolue sans créer de `/v1/`, `/v2/` qui complexifient la maintenance.
- **Subscriptions :** Mises à jour temps réel possibles (progression de campagne).
- **Valeur CV :** GraphQL est massivement adopté (Facebook, GitHub, Shopify, Airbnb).

#### Exemple concret de l'avantage

```graphql
# Une seule requête GraphQL remplace 4 appels REST
query {
  campagne(id: "uuid") {
    nom
    progression { pourcentage nbEnAttente }
    releves { statut abonneId consommation }
    factures { montant statut }
  }
}
```

#### Compromis acceptés

- L'API Gateway doit traduire chaque requête GraphQL en appels gRPC (logique de résolution).
- Les erreurs GraphQL ont une structure différente des erreurs REST (toujours HTTP 200 avec erreurs dans le corps).
- La gestion du cache est plus complexe qu'avec REST.

#### Alternatives considérées

| Alternative | Raison du rejet |
|---|---|
| REST classique | Multiples endpoints, over-fetching sur mobile, versioning complexe |
| REST + OpenAPI | Mieux documenté mais mêmes limites fondamentales que REST |
| gRPC-Web | Expérimental, support navigateur limité |

---

## ADR-004

### Titre : Django pour tous les services backend

**Date :** Juin 2026  
**Statut :** ✅ Accepté  
**Décideurs :** Équipe projet  

---

#### Contexte

Chaque microservice nécessite un framework backend. Plusieurs options Python sont disponibles : Django, FastAPI, Flask.

#### Décision

Utiliser **Django** avec Django REST Framework comme base de chaque microservice.

#### Raisons

- **Uniformité :** Un seul framework à maîtriser pour tous les services.
- **ORM puissant :** Django ORM simplifie les migrations et l'accès aux données PostgreSQL.
- **Écosystème riche :** `grpcio`, `strawberry-django`, `djangorestframework`, `django-crontab`, `reportlab` sont tous compatibles.
- **Sécurité intégrée :** Protections CSRF, injection SQL, XSS natives.
- **Admin Django :** Interface d'administration gratuite pour la gestion interne.
- **Migrations :** Système de migrations de schéma robuste et versionné.

#### Compromis acceptés

- Django est plus lourd que FastAPI (overhead de démarrage plus élevé).
- Pour des microservices purs gRPC, Django apporte des fonctionnalités web non nécessaires (templates, etc.).
- Légèrement plus de mémoire consommée par service comparé à FastAPI.

#### Alternatives considérées

| Alternative | Raison du rejet |
|---|---|
| FastAPI | Plus léger et performant, mais moins d'écosystème, équipe moins familière |
| Flask | Trop minimaliste, nécessite trop de librairies tierces |
| Mix Django + FastAPI | Complexité de maintenance avec deux frameworks différents |

---

## ADR-005

### Titre : PostgreSQL par service (Base de données dédiée)

**Date :** Juin 2026  
**Statut :** ✅ Accepté  
**Décideurs :** Équipe projet  

---

#### Contexte

En architecture microservices, la question de la gestion des données est centrale. Faut-il une base de données partagée entre tous les services ou une base par service ?

#### Décision

Chaque microservice possède sa **propre instance PostgreSQL dédiée**. Aucun service n'accède directement à la base de données d'un autre service.

#### Raisons

- **Isolation totale :** Un service ne peut pas altérer les données d'un autre.
- **Évolutivité indépendante :** Chaque base peut être optimisée selon les besoins du service.
- **Principe Database per Service :** Pattern fondamental des microservices (Sam Newman, "Building Microservices").
- **Pas de couplage de schéma :** Un service peut faire évoluer son schéma sans impacter les autres.
- **PostgreSQL :** Robuste, open source, excellent support des UUID, JSONB, et types avancés.

#### Compromis acceptés

- 8 instances PostgreSQL à gérer sur le MacBook (consommation mémoire plus élevée).
- Pas de jointures cross-service possibles (les données cross-services passent par gRPC).
- Les transactions distribuées sont complexes (pattern Saga requis si nécessaire).
- Sauvegardes à orchestrer pour chaque base séparément.

#### Alternatives considérées

| Alternative | Raison du rejet |
|---|---|
| Base de données partagée unique | Anti-pattern microservices, couplage fort entre services |
| SQLite | Insuffisant pour la production, pas de connexions concurrentes robustes |
| MongoDB | PostgreSQL mieux adapté aux données structurées et relationnelles du domaine |

---

## ADR-006

### Titre : Kubernetes + Minikube pour l'orchestration

**Date :** Juin 2026  
**Statut :** ✅ Accepté  
**Décideurs :** Équipe projet  

---

#### Contexte

9 microservices doivent être déployés, gérés et orchestrés. Une solution d'orchestration est nécessaire pour gérer les conteneurs, la découverte de services et les déploiements.

#### Décision

Utiliser **Kubernetes avec Minikube** sur le MacBook Pro comme environnement d'orchestration.

#### Raisons

- **Standard industriel :** Kubernetes est le standard de facto pour l'orchestration de conteneurs.
- **Minikube :** Implémentation légère et officielle de Kubernetes pour le développement local, parfaitement compatible avec macOS.
- **Découverte de service native :** Les services gRPC se découvrent via les Services Kubernetes (ClusterIP).
- **Gestion déclarative :** Les fichiers YAML décrivent l'état désiré du système.
- **Canary Deployment natif :** Kubernetes supporte le déploiement Canary via les labels et selectors.
- **Valeur CV :** Kubernetes est la compétence DevOps la plus demandée du marché.
- **Isolation :** Chaque service tourne dans son propre pod, isolé des autres.

#### Compromis acceptés

- Courbe d'apprentissage significative (YAML, kubectl, concepts Kubernetes).
- Consommation de ressources MacBook plus élevée (Minikube + 9 pods + 8 PostgreSQL).
- Complexité de configuration initiale (Services, Deployments, StatefulSets, PVCs, Secrets).
- En cas de panne du MacBook, l'application est totalement inaccessible.

#### Alternatives considérées

| Alternative | Raison du rejet |
|---|---|
| Docker Compose uniquement | Plus simple mais pas d'orchestration avancée, pas de Canary natif |
| Docker Swarm | Moins de fonctionnalités que Kubernetes, moins valorisé sur CV |
| k3s | Alternative légère à Kubernetes, mais Minikube est plus standard pour le dev |

---

## ADR-007

### Titre : Canary Deployment comme stratégie de déploiement

**Date :** Juin 2026  
**Statut :** ✅ Accepté  
**Décideurs :** Équipe projet  

---

#### Contexte

Plusieurs stratégies de déploiement existent pour mettre à jour les services sans interruption. Les principales sont : Blue-Green, Rolling Update, Canary, et Feature Flags.

#### Décision

Adopter le **Canary Deployment** comme stratégie principale de mise à jour des services.

#### Raisons

- **Risque minimal :** La nouvelle version est exposée progressivement (10% → 50% → 100%), limitant l'impact d'un bug.
- **Rollback immédiat :** En cas de problème, scale à 0 de la version Canary restaure 100% du trafic sur la version stable.
- **Validation en conditions réelles :** La nouvelle version est testée avec du vrai trafic avant promotion complète.
- **Adoption massive :** Utilisée par Google, Netflix, Amazon — la plus répandue en production aujourd'hui.
- **Natif Kubernetes :** Implémentable avec les primitives Kubernetes de base (labels, selectors, replicas).

#### Processus de déploiement Canary

```
Étape 1 : v1 stable → 100% trafic
Étape 2 : v2 canary → 10% trafic, v1 → 90%
Étape 3 : Monitoring 15-30 min → aucune erreur
Étape 4 : v2 → 50%, v1 → 50%
Étape 5 : v2 → 100%, v1 supprimée ✅
```

#### Compromis acceptés

- Plus complexe à implémenter que le Rolling Update (Kubernetes par défaut).
- Nécessite un monitoring pour décider de la promotion.
- Deux versions coexistent temporairement (compatibilité du schéma de données à garantir).

#### Alternatives considérées

| Alternative | Raison du rejet |
|---|---|
| Blue-Green | Nécessite le double de ressources (deux environnements complets), trop coûteux pour un MacBook |
| Rolling Update | Moins de contrôle que Canary, déploiement de Kubernetes par défaut mais moins formateur |
| Feature Flags | Complémentaire mais nécessite une infrastructure additionnelle |

---

## ADR-008

### Titre : Angular PWA mobile-first

**Date :** Juin 2026  
**Statut :** ✅ Accepté  
**Décideurs :** Équipe projet  

---

#### Contexte

Les agents de terrain utilisent l'application depuis leur smartphone pour saisir les index. Le frontend doit être accessible et utilisable sur mobile sans passer par les stores (Google Play, App Store).

#### Décision

Développer le frontend en **Angular** avec le mode **PWA (Progressive Web App)** activé, en adoptant une approche **mobile-first**.

#### Raisons

- **Angular :** Framework TypeScript robuste, fortement typé, adapté aux grandes applications.
- **Apollo Client :** Intégration GraphQL native et mature pour Angular.
- **PWA :** Installable sur l'écran d'accueil du smartphone sans passer par les stores.
- **Mobile-first :** L'interface agent est conçue d'abord pour les petits écrans, puis adaptée pour le bureau.
- **Zéro coût de distribution :** Pas de frais de développeur App Store ou Play Store.
- **Mise à jour instantanée :** Une mise à jour du frontend est disponible immédiatement, sans validation store.
- **Offline partiel :** Les PWA supportent le cache des ressources statiques (l'agent peut charger la liste des abonnés avant d'aller sur le terrain).

#### Compromis acceptés

- Performance légèrement inférieure à une application native.
- Accès limité à certaines APIs hardware (caméra, GPS) comparé à une app native.
- Compatibilité Safari iOS légèrement moins complète que sur Chrome Android.

#### Alternatives considérées

| Alternative | Raison du rejet |
|---|---|
| React | Angular déjà choisi, cohérence de l'équipe |
| Application mobile native (iOS/Android) | Coût de développement double, frais stores, délais de validation |
| React Native | Nécessite un build natif, complexité supplémentaire |
| Vue.js | Angular mieux adapté aux grandes applications d'entreprise |

---

## ADR-009

### Titre : MacBook Pro + ngrok comme serveur

**Date :** Juin 2026  
**Statut :** ✅ Accepté  
**Décideurs :** Équipe projet  

---

#### Contexte

Le projet a une contrainte budgétaire stricte : l'hébergement doit être gratuit. Les options sont un hébergeur cloud gratuit ou l'utilisation du matériel existant.

#### Décision

Utiliser le **MacBook Pro comme serveur physique** avec **ngrok** pour exposer l'application sur Internet via un tunnel HTTPS.

#### Raisons

- **Zéro coût :** Aucun frais d'hébergement.
- **Puissance suffisante :** Un MacBook Pro moderne supporte Minikube + 9 pods + 8 PostgreSQL.
- **ngrok :** Crée un tunnel HTTPS sécurisé en une commande, accessible depuis n'importe où sur Internet.
- **Contrôle total :** Accès complet au hardware, pas de restrictions de cloud provider.
- **Idéal pour la démo CV :** L'application est accessible en ligne pour les recruteurs.

#### Configuration ngrok

```bash
# Démarrage du tunnel
ngrok http 8000

# URL publique générée (tier gratuit)
https://xyz-abc.ngrok.io → localhost:8000 (API Gateway)
```

#### Compromis acceptés

- Si le MacBook est éteint ou en veille, l'application est inaccessible.
- L'URL ngrok change à chaque redémarrage (tier gratuit) — nécessite de communiquer la nouvelle URL.
- Pas de SLA ni de garantie de disponibilité.
- Performances limitées par la connexion Internet du MacBook.

#### Plan de continuité

- ngrok configuré en autostart au démarrage du MacBook.
- Script de redémarrage documenté pour relancer Minikube + ngrok en cas de panne.

#### Alternatives considérées

| Alternative | Raison du rejet |
|---|---|
| PythonAnywhere (gratuit) | Ne supporte pas plusieurs microservices, incompatible avec Kubernetes |
| Render / Railway (gratuit) | Tier gratuit trop limité pour 9 services + 8 PostgreSQL |
| VPS cloud (payant) | Hors contrainte budgétaire |
| Heroku | Plus gratuit depuis 2022 |

---

## ADR-010

### Titre : Telnyx comme fournisseur WhatsApp Business API

**Date :** Juin 2026  
**Statut :** ✅ Accepté  
**Décideurs :** Équipe projet  

---

#### Contexte

L'envoi de factures et de notifications via WhatsApp nécessite un accès à l'API WhatsApp Business. Meta ne donne pas d'accès direct aux petites structures — un Business Solution Provider (BSP) est nécessaire.

#### Décision

Utiliser **Telnyx** comme BSP pour l'accès à l'API WhatsApp Business.

#### Raisons

- **Aucun abonnement mensuel fixe :** Telnyx facture uniquement le markup par message ($0.004/message) + les frais Meta.
- **Coût réel estimé :** Environ $1/mois pour 50 abonnés (frais Meta ~$0.40 + markup ~$0.20).
- **API bien documentée :** Documentation claire, SDKs disponibles.
- **Envoi de fichiers :** Supporte l'envoi de PDF en pièce jointe (nécessaire pour les factures).
- **Pas de setup fee :** Aucun frais d'installation ou d'onboarding.
- **Markup le plus bas :** Parmi les BSPs sans abonnement, Telnyx offre le markup par message le plus compétitif.

#### Modèle de coût détaillé

```
Pour 50 abonnés, 1 envoi/mois :
  Frais Meta (Cameroun, catégorie utility) : ~$0.008 × 50 = $0.40
  Markup Telnyx                             : $0.004 × 50 = $0.20
  TOTAL mensuel                             : ~$0.60/mois
```

#### Compromis acceptés

- Dépendance à un service tiers payant (même si très peu cher).
- En cas de blocage du compte WhatsApp Business par Meta, l'envoi est interrompu.
- Nécessite une clé API à sécuriser dans Kubernetes Secrets.

#### Alternatives considérées

| Alternative | Coût | Raison du rejet |
|---|---|---|
| WhatsApp Business API officielle (Meta direct) | Gratuit frais Meta | Accès difficile sans BSP agréé |
| whatsapp-web.js | Gratuit | Non officiel, risque de blocage de compte — évaluation en parallèle (ADR-016) |
| Twilio | $0.005/msg + Meta | Légèrement plus cher que Telnyx, abonnement plateforme |
| Gupshup | $0.001/msg | Dashboard orienté Asie, moins adapté à l'Afrique |
| Lien wa.me | Gratuit | Envoi non automatique, interaction manuelle requise |

---

## ADR-011

### Titre : ReportLab pour la génération de PDF

**Date :** Juin 2026  
**Statut :** ✅ Accepté  
**Décideurs :** Équipe projet  

---

#### Contexte

Les factures doivent être générées au format PDF, incluant le logo de la société, les informations de l'abonné, les index, la consommation et le montant.

#### Décision

Utiliser **ReportLab** comme librairie Python de génération de PDF.

#### Raisons

- **100% Python :** S'intègre nativement dans Django sans dépendances système.
- **Open source :** Gratuit, licence BSD.
- **Puissant :** Gère le positionnement précis, les images (logo), les tableaux, les polices.
- **Stable et mature :** Librairie utilisée en production depuis plus de 20 ans.
- **Pas de navigateur requis :** Contrairement à WeasyPrint, ne nécessite pas de moteur de rendu HTML.
- **Performance :** Génération rapide, adapté à un traitement par lot (50 factures à la clôture).

#### Compromis acceptés

- API bas niveau — la mise en page est codée programmatiquement (pas de template HTML).
- Courbe d'apprentissage pour la mise en page complexe.
- Moins flexible que WeasyPrint pour les designs HTML/CSS.

#### Alternatives considérées

| Alternative | Raison du rejet |
|---|---|
| WeasyPrint | Dépendances système (Cairo, Pango) complexes à gérer dans Kubernetes |
| wkhtmltopdf | Nécessite un binaire externe, difficile à conteneuriser |
| fpdf2 | Moins puissant que ReportLab pour les mises en page complexes |
| pdfrw | Uniquement pour manipuler des PDFs existants, pas pour en créer |

---

## ADR-012

### Titre : Lien tokenisé pour l'accès à l'espace abonné

**Date :** Juin 2026  
**Statut :** ✅ Accepté  
**Décideurs :** Équipe projet  

---

#### Contexte

Chaque facture envoyée par WhatsApp doit inclure un accès à l'historique de l'abonné. Deux approches ont été évaluées : un OTP (mot de passe à usage unique) ou un lien tokenisé.

#### Décision

Utiliser un **lien tokenisé avec expiration** (UUID v4 + date d'expiration de 20 jours) dans le message WhatsApp, avec affichage de la date d'expiration explicite.

#### Format du lien

```
https://[domaine]/espace/[token-uuid-v4]

Exemple dans le message WhatsApp :
"🔗 Consultez votre historique :
https://eau.societe.cm/espace/a1b2c3d4-e5f6-...

(Lien valable jusqu'au 21/07/2026)"
```

#### Raisons

- **Zéro friction :** L'abonné clique simplement sur le lien, sans saisir de code.
- **Sécurisé :** UUID v4 de 128 bits, imprévisible et non devinable par force brute.
- **Expiration configurable :** 20 jours par défaut, suffisant pour consulter après réception.
- **Date explicite :** L'affichage de la date d'expiration (et non du délai) est plus compréhensible.
- **Révocable :** L'admin peut invalider un token depuis l'interface.
- **Pas de compte nécessaire :** L'abonné accède sans créer de compte.

#### Compromis acceptés

- Si le lien est partagé avec un tiers, celui-ci peut accéder à l'historique.
- Après expiration, l'abonné doit demander un renvoi de facture pour obtenir un nouveau lien.

#### Alternatives considérées

| Alternative | Raison du rejet |
|---|---|
| OTP (6 chiffres, SMS/WhatsApp) | Friction utilisateur élevée (saisie du code), UX inférieure |
| Compte abonné permanent | Complexité de gestion (mot de passe oublié, etc.), hors périmètre |
| Lien permanent sans expiration | Risque de sécurité si le lien est partagé ou intercepté |
| QR Code | Inutile — le lien dans WhatsApp est déjà cliquable |

---

## ADR-013

### Titre : Rejet de SQLite en faveur de PostgreSQL

**Date :** Juin 2026  
**Statut :** ✅ Accepté  
**Décideurs :** Équipe projet  

---

#### Contexte

SQLite a été initialement envisagé pour sa simplicité (aucune configuration, fichier unique) dans ce contexte de faible volume (50 abonnés).

#### Décision

Rejeter SQLite et utiliser **PostgreSQL** pour tous les services.

#### Raisons

- **Concurrence :** SQLite ne gère pas bien les connexions concurrentes (verrous en écriture) — problématique avec plusieurs pods Kubernetes.
- **UUID natif :** PostgreSQL supporte nativement le type UUID (`gen_random_uuid()`).
- **Types avancés :** JSONB, types temporels avec timezone, types numériques précis (DECIMAL).
- **Stratégies DevOps :** PostgreSQL supporte les sauvegardes, la réplication et la haute disponibilité — prévu pour la V2.
- **Blue-Green / Canary :** Les migrations de schéma sont mieux gérées avec PostgreSQL.
- **Standard professionnel :** PostgreSQL est la base de données relationnelle open source la plus utilisée en entreprise.

#### Compromis acceptés

- Une instance PostgreSQL par service (8 instances) contre un seul fichier SQLite.
- Configuration initiale plus complexe (StatefulSets Kubernetes, PVCs, credentials).
- Consommation mémoire plus élevée sur le MacBook.

---

## ADR-014

### Titre : Rejet du monolithe en faveur des microservices

**Date :** Juin 2026  
**Statut :** ✅ Accepté  
**Décideurs :** Équipe projet  

---

#### Contexte

Avec seulement 50 abonnés, un monolithe Django serait techniquement suffisant et bien plus simple à développer et maintenir.

#### Décision

Rejeter l'architecture monolithique et adopter les **microservices** malgré la faible volumétrie.

#### Raisons

- **Objectif CV :** Ce projet a pour but principal de démontrer des compétences techniques avancées à des recruteurs. Un monolithe ne permettrait pas de montrer la maîtrise des architectures distribuées.
- **Apprentissage :** Le projet est une opportunité délibérée d'apprendre Kubernetes, gRPC, GraphQL dans un contexte réel.
- **Isolation des responsabilités :** Même à petite échelle, la séparation claire des domaines (facturation séparée des paiements, etc.) produit un meilleur design.
- **Extensibilité :** Si le nombre d'abonnés croît ou si de nouveaux modules sont ajoutés, l'architecture est déjà prête.

#### Compromis acceptés

Complexité opérationnelle significativement plus élevée pour un volume qui ne le justifie pas fonctionnellement. Ce compromis est accepté délibérément pour les bénéfices d'apprentissage et de valorisation CV.

---

## ADR-015

### Titre : Rejet de REST en faveur de GraphQL

**Date :** Juin 2026  
**Statut :** ✅ Accepté  
**Décideurs :** Équipe projet  

---

#### Contexte

REST était l'option par défaut pour l'API externe. GraphQL a été évalué comme alternative.

#### Décision

Rejeter REST et adopter **GraphQL** comme interface API externe, implémenté avec Strawberry (Python) et Apollo Client (Angular).

#### Raisons principales

- **Over-fetching évité :** Sur réseau mobile (agents terrain), recevoir uniquement les champs nécessaires est critique.
- **Requêtes imbriquées :** Une seule requête GraphQL peut récupérer un abonné + son compteur + ses factures + ses paiements.
- **Endpoint unique :** `/graphql` au lieu de `/api/abonnes/`, `/api/compteurs/`, `/api/factures/`, etc.
- **Évolution sans version :** Nouveaux champs ajoutables sans casser les clients existants.
- **Introspection :** Angular (Apollo Client) connaît automatiquement le schéma complet.

#### Compromis acceptés

- Complexité de mise en cache plus élevée (les requêtes POST ne sont pas mises en cache par défaut par HTTP).
- L'API Gateway doit résoudre chaque champ GraphQL via des appels gRPC.
- Courbe d'apprentissage pour les développeurs non familiers avec GraphQL.

---

## ADR-016

### Titre : whatsapp-web.js comme alternative gratuite à Telnyx

**Date :** Juin 2026  
**Statut :** 🔄 En évaluation  
**Décideurs :** Équipe projet  

---

#### Contexte

Telnyx est le fournisseur WhatsApp choisi (ADR-010), mais ses frais (même minimes) ne sont pas nuls. whatsapp-web.js offre une alternative totalement gratuite.

#### Décision en cours

Évaluer **whatsapp-web.js** en parallèle comme alternative gratuite, avec une décision finale après tests.

#### Description de la solution

whatsapp-web.js est une librairie Node.js qui simule WhatsApp Web via Puppeteer (navigateur headless). Elle permet l'envoi automatique de messages sans passer par l'API officielle.

#### Avantages

- Totalement gratuit, aucun frais Meta ou BSP.
- Envoi automatique sans intervention humaine.
- Supporte l'envoi de fichiers (PDF).

#### Risques identifiés

- **Non officiel :** Violant potentiellement les conditions d'utilisation de WhatsApp/Meta.
- **Risque de blocage :** Meta peut bloquer le numéro si l'activité est détectée comme automatisée.
- **Dépendance à un navigateur headless :** Puppeteer est lourd (~300MB) et complexe à conteneuriser.
- **Instabilité :** La librairie peut se casser lors des mises à jour de WhatsApp Web.
- **Pas de SLA :** Aucun support ni garantie de fonctionnement.

#### Décision conditionnelle

```
Si les tests montrent une stabilité acceptable → whatsapp-web.js pour usage dev/demo
Si le risque de blocage est trop élevé         → Telnyx en production
```

#### Alternatives dans cette catégorie

| Solution | Coût | Risque |
|---|---|---|
| whatsapp-web.js | Gratuit | Élevé (non officiel) |
| Lien wa.me | Gratuit | Faible (officiel, manuel) |
| Telnyx | ~$1/mois | Faible (officiel) |

---

## ADR-017

### Titre : Strawberry comme librairie GraphQL Python

**Date :** Juin 2026  
**Statut :** ✅ Accepté  
**Décideurs :** Équipe projet  

---

#### Contexte

Plusieurs librairies Python permettent de créer des APIs GraphQL : Graphene, Strawberry, Ariadne.

#### Décision

Utiliser **Strawberry** comme librairie GraphQL dans l'API Gateway.

#### Raisons

- **Type hints Python :** Strawberry utilise les dataclasses et annotations Python, rendant le code plus lisible et maintenable.
- **Intégration Django native :** `strawberry-django` s'intègre directement avec les modèles Django.
- **Moderne :** Strawberry est la librairie GraphQL Python la plus active et maintenue en 2026.
- **Auto-documentation :** Le schéma est généré automatiquement depuis les types Python.
- **Support async :** Strawberry supporte nativement async/await pour les resolvers.

#### Exemple de définition d'un type

```python
import strawberry

@strawberry.type
class Abonne:
    id: strawberry.ID
    numero_abonne: str
    nom: str
    prenom: str
    statut: StatutAbonne
```

#### Alternatives considérées

| Alternative | Raison du rejet |
|---|---|
| Graphene | Plus ancienne, syntaxe plus verbeuse, moins bien maintenue |
| Ariadne | Schema-first (SDL), nécessite de maintenir un fichier .graphql séparé |

---

## ADR-018

### Titre : API Gateway sans base de données

**Date :** Juin 2026  
**Statut :** ✅ Accepté  
**Décideurs :** Équipe projet  

---

#### Contexte

L'API Gateway est un service Django. Doit-il avoir sa propre base de données PostgreSQL comme les autres services ?

#### Décision

L'API Gateway **n'a pas de base de données**. Il est purement stateless.

#### Raisons

- **Rôle du Gateway :** Traduire et router les requêtes. Il ne possède aucune donnée applicative.
- **Stateless par nature :** Chaque requête est indépendante. Aucune information n'est persistée entre les requêtes.
- **Scalabilité :** Un service stateless peut être répliqué horizontalement sans problème de synchronisation.
- **Simplicité :** Pas de migration, pas de schéma, pas de backup à gérer pour le Gateway.
- **Séparation des responsabilités :** La logique de données appartient aux services métier, pas au Gateway.

#### Ce que le Gateway stocke (en mémoire uniquement)

- Le pool de connexions gRPC vers chaque service.
- Le cache des clés publiques JWT (pour validation des tokens sans appel gRPC).

#### Compromis acceptés

- Chaque requête nécessite un appel gRPC vers Auth Service pour valider le JWT (ou cache en mémoire).
- Les logs sont en sortie standard (stdout), pas en base.

---

## ADR-019

### Titre : Reporting Service en read-only aggregator

**Date :** Juin 2026  
**Statut :** ✅ Accepté  
**Décideurs :** Équipe projet  

---

#### Contexte

Le tableau de bord doit afficher des données agrégées provenant de plusieurs services (campagnes, factures, paiements). Deux approches sont possibles : interroger chaque service à la demande, ou maintenir un agrégat pré-calculé.

#### Décision

Le Reporting Service est un **agrégateur en lecture seule** avec sa propre base de données dénormalisée, alimentée par les événements des autres services. Il ne répond qu'à des queries (pas de mutations).

#### Raisons

- **Performance :** Une seule requête gRPC au Reporting Service retourne toutes les statistiques du tableau de bord, au lieu de 5-6 appels gRPC parallèles à différents services.
- **Découplage :** Les autres services n'ont pas à exposer des endpoints de reporting complexes.
- **Données pré-calculées :** Les agrégats (totaux, pourcentages, taux) sont calculés à la réception des événements, pas à chaque lecture.
- **Pattern CQRS (Command Query Responsibility Segregation) :** Le Reporting Service implémente le côté Query de ce pattern.

#### Fonctionnement

```
Service A émet un événement gRPC → Reporting Service consomme
Reporting Service met à jour ses tables dénormalisées
API Gateway query Reporting Service → réponse instantanée
```

#### Compromis acceptés

- Légère latence de cohérence (eventual consistency) : les stats peuvent être en retard de quelques secondes.
- Duplication des données (les stats sont des dérivés des données sources).
- Le Reporting Service doit être résilient aux événements manqués (idempotence).

---

## ADR-020

### Titre : Fusion du service Compteurs dans le service Abonnés

**Date :** Juin 2026  
**Statut :** ✅ Accepté  
**Décideurs :** Équipe projet  

---

#### Contexte

La spécification initiale prévoyait un service Compteurs séparé. L'analyse a révélé que les compteurs n'ont pas de cycle de vie indépendant des abonnés.

#### Décision

Fusionner la gestion des compteurs dans l'**Abonné Service**.

#### Raisons

- **Un abonné = un compteur (toujours) :** Il n'existe aucun cas où un compteur existe sans abonné, ni un abonné sans compteur.
- **Pas de domaine indépendant :** Un compteur n'a pas de logique métier propre nécessitant un service séparé.
- **Éviter les appels gRPC inutiles :** Afficher les infos d'un abonné ne nécessite pas un appel réseau supplémentaire vers un service Compteurs.
- **Principe de cohésion :** Les entités fortement couplées appartiennent au même service.
- **Réduction de la complexité :** Un service de moins à déployer, maintenir et monitorer.

#### Principe appliqué

> "Un microservice doit encapsuler un domaine métier cohérent, pas nécessairement une table de base de données."

---

## ADR-021

### Titre : Fusion de la Tarification dans le service Facturation

**Date :** Juin 2026  
**Statut :** ✅ Accepté  
**Décideurs :** Équipe projet  

---

#### Contexte

La spécification initiale prévoyait un service Tarification séparé pour gérer le prix du m³.

#### Décision

Fusionner la tarification dans le **Facturation Service**.

#### Raisons

- **Consommateur unique :** Seul le Facturation Service utilise le tarif pour calculer les montants.
- **Pas de domaine indépendant :** La tarification est une règle de calcul interne à la facturation.
- **Faible complexité :** Un seul tarif actif à la fois (500 FCFA/m³), pas de logique de tarification complexe.
- **Cohésion métier :** La tarification et la facturation sont intrinsèquement liées.
- **Réduction de la complexité :** Un service de moins, un appel gRPC de moins par facture générée.

---

## ADR-022

### Titre : Renommage de WhatsApp Service en Notification Service

**Date :** Juin 2026  
**Statut :** ✅ Accepté  
**Décideurs :** Équipe projet  

---

#### Contexte

Le service initialement nommé "WhatsApp Service" était trop spécifique à un canal de communication.

#### Décision

Renommer en **Notification Service**.

#### Raisons

- **Extensibilité :** Demain, si des SMS ou des emails sont ajoutés comme canal, le service accueille ces nouvelles fonctionnalités sans renommage.
- **Abstraction :** Le service gère la notification, pas le canal spécifique. WhatsApp est simplement le canal actif.
- **Bonne pratique de nommage :** Un service doit être nommé selon sa responsabilité fonctionnelle, pas son implémentation technique.
- **Séparation des préoccupations :** La logique de construction du message est séparée du mécanisme d'envoi (Telnyx).

---

## ADR-023

### Titre : Prix du m³ copié dans la facture au moment de la génération

**Date :** Juin 2026  
**Statut :** ✅ Accepté  
**Décideurs :** Équipe projet  

---

#### Contexte

Le tarif peut être modifié par l'admin. Faut-il que les factures fassent référence au tarif actif, ou faut-il copier le prix dans chaque facture ?

#### Décision

Le prix du m³ est **copié** dans chaque facture au moment de sa génération. La facture est ainsi immutable.

#### Raisons

- **Intégrité des données :** Une facture représente un engagement contractuel à un instant T. Son montant ne doit pas changer si le tarif évolue.
- **Traçabilité :** L'historique des tarifs + le prix copié dans la facture permettent de toujours reconstituer le calcul.
- **Simplicité de recalcul :** Recalculer une facture se fait uniquement avec les données de la facture elle-même, sans dépendance au tarif actuel.
- **Règle comptable :** En comptabilité, une facture émise est définitive.

#### Implémentation

```python
# Au moment de la génération
facture.prix_m3 = tarif_actif.prix_m3  # copie, pas référence
facture.montant = facture.consommation * facture.prix_m3
```

---

## ADR-024

### Titre : Délai de paiement de 5 jours après la date de relevé

**Date :** Juin 2026  
**Statut :** ✅ Accepté  
**Décideurs :** Équipe projet  

---

#### Contexte

Les abonnés doivent avoir un délai raisonnable pour payer leur facture après réception. Ce délai doit être défini et paramétrable.

#### Décision

La **date limite de paiement = date de relevé + 5 jours** (configurable par l'admin dans le Config Service).

#### Raisons

- **Délai standard :** 5 jours est une pratique courante dans les services publics d'eau.
- **Incitation au paiement rapide :** Un délai court réduit les impayés.
- **Configurable :** La valeur par défaut de 5 jours peut être ajustée sans redéploiement.

#### Implémentation

```python
from datetime import timedelta

facture.date_limite_paiement = (
    releve.date_releve + timedelta(days=config.delai_paiement_jours)
)
```

---

## ADR-025

### Titre : Processus de relance impayés en 4 étapes graduées

**Date :** Juin 2026  
**Statut :** ✅ Accepté  
**Décideurs :** Équipe projet  

---

#### Contexte

Les factures impayées nécessitent un processus de relance structuré et gradué, passant d'un rappel doux à une suspension de service, avec des délais configurables.

#### Décision

Mettre en place un **processus de relance en 4 étapes** avec des délais configurables et une suspension automatique optionnelle.

| Étape | Délai par défaut | Action |
|---|---|---|
| 1 | J+0 (jour du dépassement) | WhatsApp rappel doux |
| 2 | J+3 | WhatsApp rappel ferme |
| 3 | J+7 | WhatsApp avertissement + notification admin |
| 4 | J+10 | Suspension de la ligne d'eau + WhatsApp |

#### Raisons

- **Gradualité :** Une escalade progressive est plus humaine et efficace qu'une action immédiate.
- **Configurabilité :** Chaque structure peut adapter les délais à sa politique de recouvrement.
- **Suspension optionnelle :** L'option `suspension_auto` peut être désactivée pour les structures préférant gérer manuellement.
- **Terminologie adaptée :** "Ligne d'eau" plutôt que "compte" pour correspondre au vocabulaire métier du domaine.
- **Paiement partiel :** Un versement partiel suspend les relances 5 jours, donnant un délai supplémentaire sans ignorer la dette.
- **Rétablissement automatique :** La suspension est levée automatiquement dès le paiement intégral.

#### Cron job d'implémentation

```python
# ImpayeCheckerJob — s'exécute tous les jours à 8h00
def check_impayes():
    for suivi in SuiviImpaye.objects.filter(resolu_le=None):
        jours = (date.today() - suivi.date_depassement).days

        if jours >= config.delai_rappel_1 and not suivi.rappel_1_envoye:
            envoyer_relance(suivi, etape=1)
            suivi.rappel_1_envoye = True

        if jours >= config.delai_rappel_2 and not suivi.rappel_2_envoye:
            envoyer_relance(suivi, etape=2)
            suivi.rappel_2_envoye = True

        if jours >= config.delai_avertissement and not suivi.avertissement_envoye:
            envoyer_relance(suivi, etape=3)
            notifier_admin(suivi)
            suivi.avertissement_envoye = True

        if (jours >= config.delai_suspension
                and not suivi.suspension_effectuee
                and config.suspension_auto):
            suspendre_abonne(suivi.abonne_id)
            envoyer_relance(suivi, etape=4)
            suivi.suspension_effectuee = True

        suivi.save()
```

---

---

## ADR-026

### Titre : Stack d'observabilité — OpenTelemetry + Prometheus + Loki + Jaeger + Grafana

**Date :** Juin 2026  
**Statut :** ✅ Accepté  
**Décideurs :** Équipe projet  

---

#### Contexte

Une architecture de 9 microservices sans observabilité est inopérable en pratique. Diagnostiquer une lenteur, une erreur ou un comportement anormal nécessite des outils dédiés couvrant les 3 piliers de l'observabilité : Logs, Métriques et Traces distribuées.

#### Décision

Adopter la stack suivante pour l'observabilité complète du système :

| Pilier | Outil | Rôle |
|---|---|---|
| **Instrumentation** | OpenTelemetry SDK | Injection automatique dans Django + gRPC |
| **Métriques** | Prometheus | Collecte et stockage des métriques |
| **Logs** | Loki + Promtail | Agrégation centralisée des logs JSON |
| **Traces** | Jaeger | Collecte et visualisation des traces distribuées |
| **Visualisation** | Grafana | Dashboard unifié Prometheus + Loki |

#### Raisons

- **OpenTelemetry :** Standard CNCF (Cloud Native Computing Foundation) adopté par l'industrie. Instrumentations automatiques disponibles pour Django et gRPC — zéro code métier modifié.
- **Prometheus :** Standard de facto pour les métriques Kubernetes. S'intègre nativement avec Minikube.
- **Loki :** Alternative légère à Elasticsearch (ELK), conçue pour les logs Kubernetes. Parfait pour un MacBook avec ressources limitées.
- **Jaeger :** Solution open source de traces distribuées, standard CNCF, compatible OpenTelemetry.
- **Grafana :** Unifie Prometheus et Loki dans une seule interface. Évite de multiplier les outils de visualisation.
- **Stack 100% open source :** Zéro coût de licence.
- **Valeur CV :** Maîtrise de l'observabilité cloud-native — compétence très valorisée.

#### Ce que la stack permet concrètement

```
Scénario : Lenteur signalée à 14h32

Étape 1 — Grafana (Prometheus)
  → Pic de latence sur notification-service à 14h32
  → Métrique grpc_server_duration_ms > 15 000ms

Étape 2 — Grafana (Loki)
  → Log : "Telnyx API timeout after 3 retries"
  → trace_id extrait : "def456ghi789"

Étape 3 — Jaeger
  → Trace complète de la requête sur 4 services
  → notification-service : 15s (timeout Telnyx)
  → Cause identifiée et corrigée
```

#### Impact sur l'architecture Kubernetes

4 nouveaux pods ajoutés dans le namespace `facturation-eau` :

```
prometheus-deployment    (métriques)
loki-deployment          (logs)
promtail-daemonset       (collecte logs des pods)
jaeger-deployment        (traces, mode all-in-one)
grafana-deployment       (visualisation)
```

#### Compromis acceptés

- 4-5 pods supplémentaires sur le MacBook (consommation mémoire additionnelle ~1-2 GB).
- Configuration initiale non triviale (datasources Grafana, rules Prometheus, etc.).
- Jaeger en mode all-in-one (pas de haute disponibilité) — suffisant pour ce contexte.
- Loki sans réplication — données de logs perdues en cas de crash du pod (acceptable en développement).

#### Alternatives considérées

| Alternative | Raison du rejet |
|---|---|
| ELK Stack (Elasticsearch + Logstash + Kibana) | Trop lourd pour un MacBook (Elasticsearch consomme 4-8 GB RAM) |
| Datadog / New Relic | Solutions SaaS payantes, hors contrainte budgétaire |
| Zipkin (à la place de Jaeger) | Jaeger plus complet, mieux intégré avec OpenTelemetry |
| Pas d'observabilité | Inacceptable en architecture microservices — impossible de diagnostiquer les pannes |

---

*Fin du document ADR — Système de Gestion de Facturation d'Eau*  
*Toute nouvelle décision architecturale doit faire l'objet d'un ADR numéroté et daté.*
