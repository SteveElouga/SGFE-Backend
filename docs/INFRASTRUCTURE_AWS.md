# Infrastructure AWS — dimensionnement, coûts et décisions

> **Nature de ce document :** choix de l'instance, des services managés et du réseau, avec les **mesures** qui les fondent et les **coûts** qui en découlent. Le déploiement lui-même — qui pousse quoi à chaque merge — est traité dans [`CHAINE_DE_LIVRAISON.md`](./CHAINE_DE_LIVRAISON.md).
> **Convention :** ce qui est **mesuré** est distingué de ce qui est **estimé** en fin de document. Les tarifs sont des ordres de grandeur publics, à revérifier au calculateur AWS avant engagement.
> **Dernière mise à jour :** 2026-08-28.

---

## Table des matières

1. [La mesure qui a changé le dimensionnement](#1-la-mesure-qui-a-changé-le-dimensionnement)
2. [Le choix de l'instance](#2-le-choix-de-linstance)
3. [Pourquoi pas Lightsail, moins cher](#3-pourquoi-pas-lightsail-moins-cher)
4. [Le piège de la NAT Gateway](#4-le-piège-de-la-nat-gateway)
5. [Bases de données : RDS ou conteneurs](#5-bases-de-données--rds-ou-conteneurs)
6. [Les rôles IAM](#6-les-rôles-iam)
7. [Région](#7-région)
8. [Le palier gratuit n'existe plus — ce que ça change](#8-le-palier-gratuit-nexiste-plus--ce-que-ça-change)
9. [Coût mensuel cible](#9-coût-mensuel-cible)
10. [Réglages applicatifs qui rendent la petite instance viable](#10-réglages-applicatifs-qui-rendent-la-petite-instance-viable)
11. [Risques d'infrastructure](#11-risques-dinfrastructure)
12. [Mesuré / estimé / vérifié](#12-mesuré--estimé--vérifié)

---

## 1. La mesure qui a changé le dimensionnement

Le 28 août 2026, le stack complet tournait en local. Relevé de `docker stats` sur les **21 conteneurs SGFE** :

| Composant | Mesuré | Part |
|---|---:|---:|
| **whatsapp-service** (Chromium) | **929 Mo** | **57 %** |
| redis (dont la session WhatsApp) | 162 Mo | 10 % |
| facturation-service | 102 Mo | 6 % |
| gateway (daphne) | 90 Mo | 5 % |
| auth-service | 59 Mo | 4 % |
| 6 autres services Django | 194 Mo | 12 % |
| **8 PostgreSQL réunis** | **98 Mo** | **6 %** |
| nginx + db-backup | 6 Mo | — |
| **Total conteneurs** | **1 640 Mo** | |
| Système + dockerd (estimé) | ≈ 450 Mo | |
| **Total machine, au repos** | **≈ 2,1 Go** | |

**Deux conclusions renversent les hypothèses de départ.**

### Les huit PostgreSQL ne coûtent rien

98 Mo à eux huit — **12 Mo chacun**. L'estimation initiale les chiffrait à 1,36 Go, soit **quatorze fois trop**. La justification « sortir les bases vers RDS libère de la RAM et permet une instance plus petite » **est donc fausse** : elle ne libère que 6 % du total. RDS reste défendable, mais pour ce qu'il achète réellement — voir [§5](#5-bases-de-données--rds-ou-conteneurs).

### Chromium est la moitié de la facture

929 Mo sur 1 640. **La taille de l'instance est dictée par un navigateur headless.** Sans lui, un plan à 2 Go suffirait. C'est le prix réel de l'auto-hébergement WhatsApp : environ **14 $/mois** d'écart entre une instance 2 Go et une 4 Go.

Ce n'est pas un argument contre `whatsapp-web.js` — le service est gratuit là où une API officielle serait facturée au message. C'est un chiffre utile à connaître.

> ⚠️ **Ces relevés sont pris au repos, avec 19 abonnés en base.** Le jour de la campagne de facturation, WeasyPrint génère des centaines de PDF ; et une session `whatsapp-web.js` enfle sur plusieurs jours. Le dimensionnement ci-dessous vise le **pic**, pas le repos.

---

## 2. Le choix de l'instance

| Scénario | Instance | vCPU / RAM | ≈ $/mois | Verdict |
|---|---|---|---:|---|
| **Retenu** | **`t4g.medium`** (Graviton) | 2 / 4 Go | **27** | 1,9 Go de marge pour le pic |
| Trop juste | `t4g.small` | 2 / 2 Go | 13 | 2,1 Go au repos — zéro marge |
| Surdimensionné | `t4g.large` | 2 / 8 Go | 54 | fondé sur l'estimation erronée |
| Équivalent Intel | `t3.medium` | 2 / 4 Go | 33 | ≈ 20 % plus cher à performance égale |

**Graviton (`t4g`) plutôt qu'Intel (`t3`).** Les tarifs publics donnent `t4g.medium` à ≈ 0,0336 $/h contre ≈ 0,0416 $/h pour `t3.medium`, soit **≈ 19 % d'écart**. AWS annonce jusqu'à 40 % de meilleur rapport prix/performance sur les charges *burstable*.

### Le prérequis : produire des images arm64

**Tant que la CI ne construit que pour amd64, Graviton n'est pas une option** — on paie 20 % de plus sur l'instance sans même pouvoir arbitrer. C'est le seul verrou, et il n'est pas technique.

Vérification des cinq dépendances à compilation native : **aucune ne bloque arm64.**

| Paquet | Version épinglée | Roues aarch64 |
|---|---|---|
| `psycopg2-binary` | 2.9.9 | ✅ vérifié sur PyPI — `manylinux_2_17_aarch64` **et** `musllinux_1_1_aarch64` pour cette version exacte |
| `grpcio` · `grpcio-tools` | 1.81.1 | ✅ |
| `cryptography` | 50.0.1 | ✅ |
| `weasyprint` | 69.0 | ✅ Python pur ; `libcairo2` et `libpango*` existent en arm64 Debian |
| `chromium` (whatsapp-service) | paquet Debian | ✅ disponible en arm64 |

Conséquence utile : **comme toutes les roues existent, `pip install` ne compile presque rien**. C'est ce qui rend l'émulation QEMU supportable, là où elle serait rédhibitoire sur un projet qui compile ses dépendances.

**L'arithmétique du passage à ARM :**

| Poste | Montant |
|---|---:|
| Économie Graviton sur l'instance | **− 7 $/mois** |
| Runners arm64 payants, si l'on retient les runners natifs (≈ 200 min/mois avec les builds sélectifs) | **+ 1 $/mois** |
| **Net** | **− 6 $/mois** |

> Le **comment** — émulation QEMU contre runners natifs, portée du cache, `cosign --recursive`, `trivy --platform` — relève du pipeline et non de l'infrastructure : il est traité dans [`CHAINE_DE_LIVRAISON.md`](./CHAINE_DE_LIVRAISON.md) §11.8.

Reste un point à valider avant de parier la production : **Chromium en arm64**. Le paquet existe, `whatsapp-web.js` n'a rien de spécifique à l'architecture, mais c'est le composant le plus fragile du système — à éprouver sur une instance de test avant de basculer.

**Mode `unlimited` et crédits CPU.** Les instances `t4g` sont *burstable*. Le profil de charge de SGFE est plat, sauf le jour de la campagne où WeasyPrint sature les cœurs. En mode `unlimited` (le défaut), l'instance ne ralentit pas et AWS facture le surplus — de l'ordre de quelques centimes pour deux heures de campagne mensuelle. Poser malgré tout une alarme CloudWatch sur `CPUSurplusCreditsCharged`.

**Fichier d'échange.** 2 Go de swap sur l'EBS (≈ 0,17 $/mois) absorbent le pic de Chromium sans payer de la RAM à l'année. C'est ce qui permet de rester en `medium` plutôt que de passer en `large`.

---

## 3. Pourquoi pas Lightsail, moins cher

Le plan Lightsail à **24 $/mois** offre 2 vCPU, 4 Go de RAM, **80 Go de SSD**, **4 To de transfert** et une IP statique incluse. Face à ≈ 35 $ pour l'assemblage EC2 équivalent, c'est moins cher, plus simple, et mieux doté en disque.

**Il est pourtant écarté, pour une seule raison.**

Lightsail **ne permet pas d'attacher un rôle d'instance IAM** comme le fait EC2. La méthode documentée pour qu'une instance Lightsail lise Secrets Manager consiste à **créer un utilisateur IAM et à déposer une clé d'accès et une clé secrète sur la machine**.

C'est exactement ce que toute l'architecture de sécurité cherche à éliminer : une **clé permanente**, qui ne tourne pas, qui n'expire pas, posée sur la machine qui héberge des données de facturation nominatives. S'ajoute la perte de **Session Manager**, donc l'obligation de rouvrir SSH.

> **Décision :** les ≈ 11 $/mois d'écart sont le prix de l'absence de clé d'accès permanente en production. On paie.

Lightsail reste pertinent pour un environnement de **préproduction** jetable, où il n'y a rien de sensible à protéger.

---

## 4. Le piège de la NAT Gateway

Un VPC « conforme aux bonnes pratiques » place les bases en sous-réseaux privés et ajoute une **NAT Gateway** pour leur accès sortant. Tarif : **0,045 $/h, soit ≈ 32,40 $/mois**, plus 0,045 $/Go traité.

**C'est davantage que l'instance elle-même.** Et le schéma classique « une NAT par zone de disponibilité » porte la note à ≈ 97 $/mois avant le moindre octet.

**SGFE n'en a pas besoin :**

| Besoin | Solution sans NAT | Coût |
|---|---|---:|
| L'EC2 sort vers Internet (GHCR, Brevo, WhatsApp) | sous-réseau **public** + Internet Gateway | 0 |
| L'EC2 lit S3 | **VPC Gateway Endpoint** pour S3 | 0 |
| RDS (si retenu) | sous-réseaux **privés**, aucun accès sortant nécessaire | 0 |
| Accès administrateur | **SSM Session Manager** via l'IGW | 0 |

> **Ne pas créer de NAT Gateway est le plus gros levier d'économie de tout ce document — et c'est une non-action.**

### Le coût de l'IPv4 publique, souvent oublié

Depuis février 2024, AWS facture **0,005 $/h, soit ≈ 3,60 $/mois, par adresse IPv4 publique** — y compris une Elastic IP *attachée*. L'époque où une EIP en service était gratuite est révolue. Ce poste est intégré au [§9](#9-coût-mensuel-cible).

---

## 5. Bases de données : RDS ou conteneurs

La mesure du [§1](#1-la-mesure-qui-a-changé-le-dimensionnement) retire l'argument mémoire. Le choix devient un arbitrage **durabilité contre coût**, à faire les yeux ouverts.

| | 8 conteneurs + snapshots | 1 RDS, 8 bases |
|---|---|---|
| Coût mensuel | ≈ **2 $** (snapshots EBS + S3) | ≈ **15 $** |
| RAM libérée sur l'EC2 | — | 98 Mo |
| Granularité de restauration | **24 h** (snapshot quotidien) | **à la seconde** (PITR) |
| Sauvegardes hors instance | oui, via S3 et snapshots | oui, gérées |
| Chiffrement au repos | à activer sur l'EBS | par défaut |
| Correctifs PostgreSQL | manuels | fenêtre gérée |
| Effort de bascule | nul | 1 jour |

**La bascule vers RDS ne coûte aucune ligne de code** — c'est vérifié. Chaque service lit ses propres variables :

```
AUTH_DB_HOST / AUTH_DB_NAME / AUTH_DB_PORT / AUTH_DB_USER / AUTH_DB_PASSWORD
ABONNE_DB_HOST / …                      … et ainsi de suite pour les huit
```

Pointer les huit `*_DB_HOST` vers un même point d'entrée RDS, avec huit bases et huit utilisateurs distincts, **préserve exactement le contrat d'isolation** : un service ne détient que les identifiants de sa propre base.

### Recommandation

**Démarrer sans RDS**, avec les huit conteneurs, et une durabilité assurée par :

- **snapshots EBS quotidiens** via Data Lifecycle Manager, rétention 7 jours, incrémentaux ;
- le script `scripts/backup-databases.sh` **existant**, dont la sortie part vers `s3://sgfe-backups` (versionnement activé, cycle de vie vers Glacier à 30 jours).

Cela donne une granularité de 24 h pour ≈ 2 $/mois. Pour une régie où le pire cas est la perte d'une journée de relevés — ressaisissables depuis les carnets papier —, c'est un compromis défendable.

**Basculer vers RDS le jour où** l'un de ces trois seuils est franchi : la perte de 24 h devient inacceptable, la charge dépasse ce qu'un conteneur non réglé encaisse, ou l'exploitation manuelle des correctifs devient un fardeau.

---

## 6. Les rôles IAM

Principe directeur : **aucune clé d'accès AWS longue durée n'existe** — ni sur l'instance, ni dans les secrets GitHub, ni dans un `.env`.

### `sgfe-ec2-role` — profil d'instance

| Permission | Portée | Usage |
|---|---|---|
| `secretsmanager:GetSecretValue` | `secret:sgfe/*` | Clés Django, mots de passe DB, RS256, Brevo, WhatsApp |
| `s3:PutObject` `GetObject` | `sgfe-factures/*` | Déporter les PDF hors du volume local |
| `s3:PutObject` | `sgfe-backups/*` | Envoyer les `pg_dump` hors de l'instance |
| `logs:PutLogEvents` | `/sgfe/*` | Agent CloudWatch |
| `AmazonSSMManagedInstanceCore` | géré par AWS | Session Manager |

**Conséquence la plus utile :** avec Session Manager, le **port 22 reste fermé**. Plus de clé privée à protéger, plus de `fail2ban`, et chaque session tracée dans CloudTrail avec l'identité de son auteur.

### `sgfe-github-deploy` — rôle OIDC

Détaillé dans [`CHAINE_DE_LIVRAISON.md`](./CHAINE_DE_LIVRAISON.md) §7, puisqu'il appartient à la chaîne de livraison. Rappel du point critique : la politique de confiance doit contraindre **le dépôt et la branche** via la condition `sub`, faute de quoi n'importe quel dépôt GitHub peut assumer le rôle.

### Ce qui ne doit pas exister

- aucun **utilisateur IAM** avec clé d'accès — utiliser IAM Identity Center pour l'accès humain ;
- aucune politique en `Resource: "*"` sur Secrets Manager ou S3 ;
- aucun `AdministratorAccess` sur un rôle applicatif ;
- compte racine : MFA matériel, puis on n'y touche plus.

---

## 7. Région

**`eu-west-3` (Paris).**

Le Cameroun est relié à l'Europe par les câbles WACS, SAT-3 et ACE, qui atterrissent à Douala. Paris est à ≈ 4 700 km de Douala ; Le Cap (`af-south-1`) à ≈ 3 900 km à vol d'oiseau, mais le routage depuis l'Afrique centrale vers l'Afrique australe passe le plus souvent **par l'Europe**, ce qui annule l'avantage géographique. `af-south-1` est en outre plus cher et n'offre pas tous les services.

`eu-west-1` (Irlande) est typiquement **8 à 12 % moins cher** que Paris. Sur ≈ 35 $/mois, l'économie est de ≈ 3,50 $ — insuffisante pour justifier la latence supplémentaire sur une application utilisée toute la journée par des agents de terrain.

> À vérifier avant de figer : mesurer la latence réelle depuis Douala vers `eu-west-3` et `eu-west-1` avec un outil de type CloudPing. Le routage réel prime sur la distance.

---

## 8. Le palier gratuit n'existe plus — ce que ça change

**Changement du 15 juillet 2025.** AWS a remplacé l'ancien palier gratuit de 12 mois par un **modèle à crédits** : les nouveaux comptes reçoivent **100 $**, portés à **200 $** en accomplissant cinq tâches d'intégration (lancer et terminer une EC2, configurer une RDS, déployer une Lambda, tester une invite Bedrock, créer un budget).

**Conséquence directe pour SGFE :**

- les **750 heures mensuelles gratuites de `t3.micro`** ne s'appliquent qu'aux comptes ouverts **avant le 15 juillet 2025** ;
- les **750 heures de `db.t4g.micro` RDS** non plus. L'idée de « RDS gratuit la première année » ne tient donc pas sur un compte récent — l'usage RDS **consomme les crédits** comme le reste ;
- ce qui reste réellement gratuit et **sans limite de durée** : le palier CloudFront (1 To sortant et 10 M de requêtes par mois), les VPC Gateway Endpoints S3, les rôles IAM, CloudTrail (trace de gestion).

**Ce que cela impose :**

1. vérifier la date d'ouverture du compte — elle change entièrement le calcul de la première année ;
2. à ≈ 35 $/mois, **200 $ de crédits représentent moins de six mois**. Prévoir la sortie de crédits, pas seulement l'entrée ;
3. **créer un budget AWS Budgets dès le premier jour**, avec alertes à 50 % et 80 %. Les crédits s'épuisent sans préavis et la facture qui suit n'est pas amortie.

---

## 9. Coût mensuel cible

Ordres de grandeur, `eu-west-3`, à la demande, hors remises.

| Poste | Configuration | ≈ $/mois |
|---|---|---:|
| EC2 | `t4g.medium`, 730 h, mode `unlimited` | 27 |
| EBS | gp3 30 Go + 2 Go de swap | 2,7 |
| Adresse IPv4 publique | 1 Elastic IP attachée | 3,6 |
| Snapshots EBS | 7 jours, incrémentaux | 1,5 |
| S3 | dumps + PDF, ≈ 20 Go | 0,5 |
| CloudFront | < 1 To — **gratuit, sans limite de durée** | 0 |
| Secrets Manager | 5 secrets à 0,40 $ | 2 |
| Route 53 | 1 zone hébergée | 0,5 |
| ACM, IAM, VPC Endpoint S3, CloudWatch de base | — | 0 |
| **NAT Gateway** | **non créée** | **0** |
| Transfert sortant | absorbé par CloudFront | 0 |
| **Total** | | **≈ 38 $** |

| Variante | Écart | Total |
|---|---|---:|
| Avec RDS `db.t4g.micro` + 20 Go | + 15 | ≈ 53 $ |
| Avec un ALB au lieu de Let's Encrypt sur l'origine | + 22 | ≈ 60 $ |
| Avec une NAT Gateway (à éviter) | + 33 | ≈ 71 $ |
| Sur `t3.medium` (Intel) au lieu de Graviton | + 6 | ≈ 44 $ |
| Avec Savings Plan 1 an sans avance, souscrit à M+3 | − 27 % sur l'EC2 | ≈ 31 $ |

### Savings Plans : quand s'engager

Un Compute Savings Plan d'un an sans avance rend ≈ 27 %. **Ne pas souscrire avant que l'architecture soit stable** — c'est-à-dire après la migration, vers M+2 ou M+3. S'engager sur une forme qu'on va changer revient à payer pour rien.

Nuance à connaître : le plan réduit la facture, donc il fait **durer les crédits plus longtemps**. C'est un argument pour ne pas trop tarder non plus.

---

## 10. Réglages applicatifs qui rendent la petite instance viable

| Réglage | État actuel | Effet attendu |
|---|---|---|
| `MALLOC_ARENA_MAX=2` | **absent des 12 Dockerfiles** | glibc alloue jusqu'à 8 arènes par processus multi-thread ; les borner réduit typiquement la RSS de 20 à 30 % sur du Python à plusieurs threads. Gratuit. |
| `max_workers` du serveur gRPC | **10 par service, ×9** | 4 suffisent largement à cette charge ; chaque thread coûte une pile et des arènes |
| Fichier d'échange | absent | 2 Go sur l'EBS absorbent le pic Chromium sans payer de RAM à l'année |
| Drapeaux Chromium | **déjà optimaux** | `--no-sandbox`, `--disable-dev-shm-usage`, `--disable-gpu`, `--no-zygote`, `--disable-extensions` — rien à gagner de plus |
| `maxmemory` sur Redis | absent | ⚠️ **ne pas en poser avec éviction** — voir [§11](#11-risques-dinfrastructure) |

> Les deux premiers réglages se posent en une PR et ne changent aucun comportement. Ils valent d'être faits **avant** de figer la taille de l'instance, puisqu'ils déplacent le seuil.

---

## 11. Risques d'infrastructure

### 11.1 La session WhatsApp vit dans Redis, pas sur le volume du conteneur

**Correction d'une erreur d'analyse initiale.** `whatsapp-service/redis-store.js` implémente un store `RemoteAuth` : la session WhatsApp est **compressée en zip et stockée dans Redis**, persistée par l'AOF sur le volume `redis_data`. Le répertoire `/app/session` n'est qu'un espace de travail où `RemoteAuth` extrait et recompresse.

**Ce qui en découle :**

- `--appendonly yes` et le volume `redis_data` sont **obligatoires**, pas un réglage par défaut à nettoyer ;
- **ne jamais poser de `maxmemory` avec une politique d'éviction** (`allkeys-lru` et apparentées) : Redis pourrait évincer la clé de session et imposer un rescan manuel du QR code. Si une limite s'impose un jour, ce sera avec `maxmemory-policy noeviction` ;
- le volume à protéger par snapshot est **`redis_data`**, pas `whatsapp_session`.

### 11.2 Le service WhatsApp reste un animal de compagnie

`whatsapp-web.js` pilote un vrai Chromium tenant une vraie session authentifiée par QR code. Cela interdit définitivement : la réplication (une session par numéro), la mise à l'échelle automatique, et tout passage propre à Fargate. Si la session est perdue, **quelqu'un doit rescanner, téléphone en main** — non automatisable.

**Parade :** snapshots quotidiens du volume `redis_data`, et un runbook écrit pour le rescan.

### 11.3 Les sauvegardes meurent avec l'instance

`db-backup` écrit ses `pg_dump` dans `./backups/` — **sur le disque de la machine qu'ils protègent**. Une perte d'instance emporte les bases *et* leurs sauvegardes. C'est le seul point où la panne est totale et irréversible.

**Parade :** expédition vers `s3://sgfe-backups` (versionnement + cycle de vie), et snapshots EBS. Même traitement pour le volume `facturation_pdfs`.

### 11.4 L'instance est mono-zone

Une seule EC2, une seule zone de disponibilité. Une panne de zone met SGFE hors service jusqu'à reconstruction. **C'est un choix assumé** : la redondance multi-zone double le coût et n'a pas de sens tant que le service WhatsApp reste non réplicable. Ce qu'il faut en revanche garantir, c'est le **temps de reconstruction** — d'où les playbooks Ansible et les snapshots.

---

## 12. Mesuré / estimé / vérifié

**Mesuré** dans les dépôts et sur le stack en fonctionnement, le 28 août 2026 :

- la consommation des 21 conteneurs (`docker stats`), dont whatsapp-service à 929 Mo et les 8 PostgreSQL à 98 Mo cumulés ;
- l'absence de `MALLOC_ARENA_MAX` dans les 12 Dockerfiles ;
- `max_workers=10` sur les serveurs gRPC ;
- les drapeaux Chromium de `whatsapp-service/server.js` ;
- le store `RemoteAuth`/Redis de `whatsapp-service/redis-store.js` ;
- l'indépendance des variables `*_DB_HOST` des huit services ;
- l'absence de tout fichier d'infrastructure dans les deux dépôts.

**Vérifié auprès de sources externes** (août 2026) : le remplacement du palier gratuit AWS par un modèle à crédits au 15 juillet 2025 ; le tarif de la NAT Gateway (0,045 $/h) ; la facturation des IPv4 publiques depuis février 2024 ; l'écart de tarif entre `t4g` et `t3` ; les plans Lightsail et **l'impossibilité d'y attacher un rôle d'instance IAM** ; l'absence d'état et de détection de dérive dans les modules AWS d'Ansible ; les distances relatives Paris / Le Cap.

**Estimé, non mesuré :** la surcharge système et dockerd (≈ 450 Mo), tous les montants en dollars, l'effet de `MALLOC_ARENA_MAX` sur ce code précis, et la consommation au pic de campagne.
