# Plan de reprise d'activité (DR) — SGFE Backend

> **Nature de ce document :** ce plan répond à une question différente du
> [`RUNBOOK.md`](./RUNBOOK.md). Le runbook répond à « un incident arrive, que
> fait-on maintenant » (diagnostic, procédures ponctuelles, un service ou une
> base à la fois). Ce document répond à « on a perdu l'infrastructure — la
> seule instance EC2, un secret compromis, une corruption découverte tard —
> comment reconstruit-on le service de zéro, et en combien de temps ». Il ne
> duplique pas le runbook : il le référence pour les procédures détaillées déjà
> écrites, et se concentre sur les scénarios de catastrophe, les objectifs de
> reprise et la séquence de reconstruction complète.
>
> **Document jumeau, pas concurrent :** [`CHAINE_DE_LIVRAISON.md`](./CHAINE_DE_LIVRAISON.md)
> décrit comment un merge sur `main` se déploie ; [`INFRASTRUCTURE_AWS.md`](./INFRASTRUCTURE_AWS.md)
> décrit le dimensionnement et les coûts. Ce document suppose ces deux-là lus,
> et décrit ce qui se passe quand la machine qu'ils ciblent disparaît.
>
> **Avertissement de lecture, à ne pas sauter :** une partie de ce qui suit
> décrit du code qui **existe et a été vérifié dans le dépôt** (scripts,
> playbooks Ansible, workflow de déploiement) mais dont l'exécution contre une
> vraie infrastructure AWS n'est **attestée nulle part dans ce dépôt** — aucun
> état Terraform (écarté par choix), aucune trace d'une instance réellement
> provisionnée, et `cd-prod.yml` échoue aujourd'hui faute des secrets GitHub
> requis (voir §5). Ce plan décrit donc la **meilleure reconstruction possible
> avec ce qui est écrit aujourd'hui**, pas une procédure déjà rejouée avec
> succès. C'est précisément pourquoi §6 (test du plan) n'est pas optionnel.
>
> **Dernière mise à jour :** 2026-09-03, sur l'état du dépôt à cette date.

---

## Table des matières

1. [Scénarios de catastrophe couverts](#1-scénarios-de-catastrophe-couverts)
2. [Objectifs de reprise (RTO/RPO)](#2-objectifs-de-reprise-rtorpo)
3. [Séquence de reconstruction complète](#3-séquence-de-reconstruction-complète)
4. [Limites actuelles honnêtes](#4-limites-actuelles-honnêtes)
5. [Test du plan](#5-test-du-plan)

---

## 1. Scénarios de catastrophe couverts

SGFE tourne aujourd'hui sur **une seule instance EC2 `t4g.medium`, une seule
zone de disponibilité** (`eu-west-3`, voir `INFRASTRUCTURE_AWS.md` §2 et §11.4
— « choix assumé »). Cette réalité borne ce qui est un scénario de DR
pertinent pour ce projet, à ce stade : pas de manuel générique, seulement ce
qui peut réellement arriver à ce système précis.

### 1.1 Perte totale de l'instance EC2

Le scénario central. Causes plausibles : panne de la zone de disponibilité
côté AWS, terminaison accidentelle (erreur d'exploitation, script de
teardown lancé sur la mauvaise cible), panne matérielle irrécupérable. Une
seule zone signifie qu'il n'y a **aucun répit automatique** — pas de
promotion, pas de bascule, pas de deuxième machine qui prend le relais. Tout
s'arrête jusqu'à reconstruction manuelle.

C'est le scénario le plus sévère parce que, comme détaillé en §4, **les
sauvegardes elles-mêmes vivent sur le disque de cette même instance** —
perdre la machine, aujourd'hui, c'est perdre les bases *et* leurs dumps dans
le même geste.

### 1.2 Corruption de données découverte tardivement

L'instance survit, mais une base contient des données corrompues depuis
plusieurs jours avant d'être remarquée. Plausible ici en particulier parce
que **l'observabilité est nulle** (`CLAUDE.md` : « AUCUNE aujourd'hui ») — un
incident se découvre par un signalement humain (un abonné, un comptable), pas
par une alerte. La fenêtre de détection peut donc dépasser un jour.

Ce scénario ne demande pas de reconstruire l'instance : il demande de
restaurer une ou plusieurs des 8 bases à un point antérieur à la corruption,
dans la limite de ce que la rétention locale conserve (voir §2).

### 1.3 Compromission de secrets

Fuite d'un ou plusieurs secrets applicatifs : `INTERNAL_GRPC_KEY` (authentifie
les appelants gRPC entre les neuf composants), les clés JWT RS256
(`auth-service`), `WHATSAPP_INTERNAL_API_KEY`, `BREVO_API_KEY`,
`BACKUP_ENCRYPTION_KEY`, ou un mot de passe PostgreSQL. Ne détruit rien en
soi, mais exige une rotation complète avant que la question « combien de
temps le système a-t-il tourné sous un secret connu de quelqu'un d'autre »
devienne sans objet.

### 1.4 Scénarios explicitement écartés

Pas d'incendie de datacenter, pas de perte multi-région, pas de plan de
continuité d'activité au sens d'un siège social évacué : ce sont des
scénarios de manuel qui ne correspondent à rien de réel pour une seule
instance EC2 dans une région gérée par AWS. La « panne de datacenter » de ce
projet, c'est le §1.1 — AWS gère déjà la résilience physique de la zone, et
la question qui reste est *ce que fait SGFE* quand cette zone-là devient
indisponible, pas comment protéger un bâtiment.

---

## 2. Objectifs de reprise (RTO/RPO)

### 2.1 RPO — ce qu'on peut perdre, honnêtement

Le service `db-backup` (`docker-compose.yml`) exécute
`scripts/backup-databases.sh` via :

```
entrypoint: ["/bin/sh", "-c", "apk add --no-cache openssl && while true; do sh /backup.sh || true; sleep 86400; done"]
```

**Ce n'est pas un cron à heure fixe.** C'est une boucle qui tourne une
première fois au démarrage du conteneur, puis toutes les 86 400 secondes
(24 h) — l'horaire **dérive à chaque redémarrage** du conteneur `db-backup`
(mise à jour, redémarrage de l'hôte). `RETENTION_DAYS=7` : les dumps plus
vieux que 7 jours sont supprimés localement.

| Scénario | RPO réel | Fondement |
|---|---|---|
| **1.2 — Corruption sur instance vivante** | **≈ 24 h**, borné à **7 jours** par la rétention locale | Intervalle entre deux passages de la boucle `db-backup` ; au-delà de J-7, le dump n'existe plus |
| **1.1 — Perte totale de l'instance** | **Perte totale et irréversible des données**, aujourd'hui | Voir ci-dessous — les dumps meurent avec la machine |
| **1.3 — Compromission de secrets** | Sans objet directement, mais les dumps déjà écrits restent chiffrés avec l'ancienne `BACKUP_ENCRYPTION_KEY` — les conserver lisibles après rotation exige de garder l'ancienne clé le temps d'épuiser la rétention | `scripts/backup-databases.sh` — une seule passphrase, aucune rotation observée dans le dépôt |

**Pourquoi le RPO du scénario 1.1 est infini aujourd'hui, vérifié dans le
code, pas supposé :**

- `docker-compose.yml` monte `./backups:/backups` dans `db-backup` — un
  bind-mount sur le disque de l'instance qui héberge aussi les 8 conteneurs
  PostgreSQL. `.gitignore` confirme `/backups/` : rien n'en sort par Git non
  plus.
- `ansible/01-infra.yml` **crée** un bucket S3 dédié (`bucket_sauvegardes`,
  chiffré AES256, versionné, cycle de vie à `retention_sauvegardes_jours`
  = 30 jours) — le commentaire du playbook lui-même le dit : *« Aujourd'hui les
  pg_dump quotidiens vivent sur le disque de l'instance et meurent avec
  elle. »*
- Mais **`scripts/backup-databases.sh` ne contient aucune ligne qui pousse
  vers S3** (vérifié — zéro occurrence de `s3`/`aws` dans ce script). Le
  bucket existe comme infrastructure ; rien ne l'alimente. Tant que cette
  ligne manque, un dump chiffré local ne protège contre rien de plus qu'une
  corruption applicative sur une instance qui reste debout (§1.2) — il ne
  survit pas à la perte de la machine qui l'a produit.

**Priorité n°1 avant tout DR crédible sur le scénario 1.1 : câbler
l'expédition de `scripts/backup-databases.sh` vers `sgfe-sauvegardes-*`.**
Sans cela, ce document décrit une reconstruction d'infrastructure vide de
données.

### 2.2 RTO — ce que la reconstruction coûte réellement en temps

**Aucun de ces chiffres n'a été mesuré en conditions réelles** — aucune trace
dans ce dépôt d'un rejeu de bout en bout des playbooks Ansible contre un
compte AWS. Ce sont des estimations composées à partir de ce qui est vérifié
pièce par pièce.

| Étape | Estimation | Fondement |
|---|---|---|
| Provisionnement VPC/EC2/S3/IAM (`ansible/01-infra.yml`, 335 lignes) | 10–20 min | Ordre de grandeur typique de l'API AWS pour ces ressources ; **non mesuré** — aucune exécution attestée dans le dépôt |
| Amorçage machine (`ansible/02-bootstrap.yml` + rôles `docker`/`secrets`/`volumes`/`compose`) | 5–10 min | Installation Docker, lecture Secrets Manager → `.env`, dépôt des fichiers compose, génération des clés JWT RS256 sur place (`community.crypto.openssl_privatekey`) — **estimé** |
| Récupération des images (`docker compose pull`) | quelques minutes | **Rapide par construction** : les images sont déjà construites, scannées (Trivy) et signées (cosign) par la CI — aucune recompilation sur la machine (`DEPLOYMENT.md` §Lancement, `--no-build`) |
| Restauration des 8 bases PostgreSQL | **quelques secondes à l'échelle actuelle** ; **non mesuré à l'échelle de production** | Voir ci-dessous |
| Régénération des certificats mTLS/TLS + clé JWT si absente | quelques secondes chacun, mais **étape manuelle** | Voir §3.4 et §4 — non automatisée par Ansible |
| Rescan QR WhatsApp | **indéterminé** — dépend de la disponibilité d'un humain avec le téléphone dédié | Non automatisable par nature (§4) |

**Sur la restauration des 8 bases — le vrai chiffre, mesuré, en lecture
seule sur la pile de développement partagée** (19 abonnés en base, contexte
identique à celui d'`INFRASTRUCTURE_AWS.md` §1) :

| Base | Taille du dernier dump (`.sql.gz`) |
|---|---:|
| auth_db | 169 Ko |
| notification_db | 20 Ko |
| campagne_db | 8 Ko |
| paiement_db | 7 Ko |
| reporting_db | 5 Ko |
| abonne_db | 5 Ko |
| facturation_db | 5 Ko |
| config_db | 2 Ko |
| **Total, 8 bases** | **≈ 220 Ko** |

À ce volume, déchiffrement + `gunzip` + `psql` pour les 8 bases se joue en
quelques secondes au total — `scripts/test-restore.sh` (qui automatise ce
parcours pour une base) le confirme empiriquement sur `config_db`, la plus
petite. **Ce chiffre ne dit rien du temps de restauration en production.**
Le jour où la base compte des milliers d'abonnés et plusieurs campagnes de
facturation, `auth_db` et `notification_db` — déjà les plus grosses à cette
échelle jouet — grossiront le plus vite. **Ce point doit être remesuré à
l'échelle réelle dès que le volume de production existe**, pas supposé
proportionnel.

**RTO consolidé — perte totale de l'instance (scénario 1.1), une fois le gap
S3 du §2.1 comblé :**

```
≈ 20-35 min  provisionnement + amorçage (estimé, non mesuré)
+ quelques minutes   pull des images signées
+ quelques secondes  à quelques minutes  restauration des 8 bases (selon volume réel)
+ quelques minutes   régénération manuelle des certificats (étape non automatisée)
──────────────────────────────────────────────────────────────────
≈ 30-50 min pour un service techniquement opérationnel (hors WhatsApp)
+ délai indéterminé, potentiellement des heures, pour le rescan WhatsApp
```

**Sans le câblage S3 (état actuel du dépôt) : RTO sans objet pour le scénario
1.1** — il n'y a rien à restaurer.

Pour le scénario 1.2 (corruption sur instance vivante) : le RTO se limite à
la procédure de restauration d'une base du runbook (`RUNBOOK.md` §3.2),
répétée pour chaque base touchée — de l'ordre de quelques minutes par base à
l'échelle actuelle, arrêt du service concerné compris.

Pour le scénario 1.3 (compromission de secrets) : pas de reconstruction
d'instance nécessaire. Le temps dominant est humain — générer et faire
approuver les nouveaux secrets dans AWS Secrets Manager, puis redéployer —
de l'ordre de 30 à 60 minutes, plus la conséquence pour les utilisateurs
d'une rotation des clés JWT RS256 (invalide tous les jetons en circulation :
tout le monde se reconnecte).

---

## 3. Séquence de reconstruction complète

Scénario traité : **perte totale de l'instance EC2** (§1.1), le plus
exigeant. Les scénarios 1.2 et 1.3 n'utilisent qu'un sous-ensemble de ces
étapes (indiqué à chaque section).

### 3.1 Provisionner une nouvelle instance

```bash
# Depuis un poste avec des identifiants AWS habilités à créer VPC/EC2/IAM/S3
cd ansible/
ansible-playbook 01-infra.yml   # VPC, sous-réseau public (pas de NAT), EC2 t4g.medium,
                                 # bucket de sauvegardes, rôles IAM (sgfe-ec2-role, sgfe-github-deploy)
ansible-playbook 02-bootstrap.yml  # Docker, session GHCR (lecture seule), lecture Secrets
                                     # Manager → /opt/sgfe/.env, dépôt des deux fichiers
                                     # compose + nginx/default.conf, génération des clés JWT
                                     # RS256 si absentes
```

Détails de dimensionnement et de choix réseau (pas de NAT Gateway, région
`eu-west-3`, coût cible) : `INFRASTRUCTURE_AWS.md`. Détail des rôles IAM et
de la connexion SSM (pas de SSH, jamais) : `INFRASTRUCTURE_AWS.md` §6 et
`CHAINE_DE_LIVRAISON.md` §7-8.

**Point de vigilance non couvert par ces playbooks** : aucune allocation
d'IP élastique ni mise à jour Route 53 (vérifié — zéro occurrence dans
`ansible/01-infra.yml`). La nouvelle instance aura une IP publique
différente de l'ancienne ; DNS et toute référence externe (`FRONTEND_URL`,
domaine du frontend) doivent être repointés **manuellement**.

*(Scénarios 1.2/1.3 : cette étape ne s'applique pas — l'instance existe déjà.)*

### 3.2 Restaurer les 8 bases PostgreSQL

**Prérequis explicite du §2.1** : ceci suppose que l'expédition hors instance
des sauvegardes a été mise en place. Sans elle, il n'y a pas de dump à
restaurer sur une instance neuve.

La commande exacte de déchiffrement + restauration, avec ses deux variantes
(chiffré `.sql.gz.enc` / historique non chiffré `.sql.gz`), est documentée
dans `RUNBOOK.md` §3.2 — ne pas la dupliquer ici. **Cette procédure cible une
base à la fois** ; pour une reconstruction complète, la rejouer pour les 8 :
`auth_db`, `abonne_db`, `campagne_db`, `facturation_db`, `paiement_db`,
`notification_db`, `config_db`, `reporting_db` (liste exacte de
`scripts/backup-databases.sh`). L'ordre entre elles est indifférent — les 8
bases sont isolées, aucune n'a de clé étrangère vers une autre (règle
fondamentale du projet, `CLAUDE.md`) ; seul compte de restaurer chaque base
avant de démarrer le service applicatif qui la lit.

Le volume `facturation_pdfs` (PDF de factures déjà générés) **n'a pas besoin
d'être restauré** : `factures/services.py::FactureService.get_pdf_bytes`
régénère un PDF à la volée s'il est absent, et la commande
`python manage.py regenerer_pdfs` permet de tout régénérer d'un coup depuis
`facturation_db` une fois celle-ci restaurée. C'est un cache, pas une source
de vérité.

*(Scénario 1.2 : seules les bases effectivement corrompues sont concernées —
pas les 8 systématiquement.)*
*(Scénario 1.3 : cette étape ne s'applique pas, sauf si la compromission a
aussi corrompu des données.)*

### 3.3 Reconstruire la session WhatsApp

La session vit dans Redis (`whatsapp-service/redis-store.js`, store
`RemoteAuth`, zip persisté par l'AOF sur le volume `redis_data` —
`INFRASTRUCTURE_AWS.md` §11.1). Ce volume est perdu avec l'instance.

Reconstruction : redémarrer `whatsapp-service`, ouvrir
**Configuration › WhatsApp & Tokens** dans le frontend, scanner le QR code
avec le téléphone dédié à WhatsApp Business tenu par l'équipe. Aucune autre
voie n'existe — pas de route `/qr` publique, pas d'automatisation possible
par nature (`whatsapp-web.js` pilote un vrai navigateur derrière une vraie
session téléphone).

**Point à ne pas passer sous silence — vérifié dans le code, pas supposé :**
un abonné dont la facture ou la relance devait partir pendant la coupure ne
la recevra pas rétroactivement une fois la session reconstruite. Le modèle
`Envoi` (factures, relances, reçus, diffusions) applique une « dégradation
gracieuse » documentée dans `services/notification/CLAUDE.md` : *« si
WhatsApp est indisponible, l'Envoi est marqué ECHEC en base sans lever
d'erreur gRPC »*. Le job de fond
(`notifications/schedulers.py::diffusion_processor_job`,
`services.py::DiffusionService.traiter_lot_en_attente`) ne retraite que les
lignes au statut `EN_ATTENTE` — un envoi déjà marqué `ECHEC` reste `ECHEC`.
**Aucun mécanisme de rattrapage automatique n'existe.** La facture reste
consultable depuis l'espace abonné (le PDF, lui, n'est pas perdu — §3.2), et
un comptable peut la renvoyer manuellement une fois la session WhatsApp
restaurée, mais rien ne le fait tout seul.

*(Scénarios 1.2/1.3 : cette étape ne s'applique que si la coupure a
effectivement emporté la session Redis — pas systématique.)*

### 3.4 Régénérer les certificats

Trois familles de secrets cryptographiques, **toutes gitignorées, donc
perdues avec l'instance**, et **aucune des trois n'est automatisée par les
playbooks Ansible actuels** (vérifié — `generate-grpc-certs.sh`,
`generate-nginx-cert.sh` et `renew-letsencrypt-cert.sh` n'apparaissent dans
aucun rôle) :

| Secret | Script | Portée |
|---|---|---|
| CA + certificat mTLS gRPC interne | `scripts/generate-grpc-certs.sh` | Partagé par les 9 composants gRPC (les 8 services + la gateway), un seul certificat serveur **et** client |
| Certificat TLS du nginx de ce dépôt | `scripts/generate-nginx-cert.sh` | Développement local uniquement |
| Certificat TLS de production | `scripts/renew-letsencrypt-cert.sh` | Cible réelle en production : CloudFront + Let's Encrypt sur l'origine EC2 (`DEPLOYMENT.md` §Lancement, note en tête de `generate-nginx-cert.sh`) |

**Seules les clés JWT RS256 de l'auth-service sont automatisées** — le rôle
`compose` (`ansible/roles/compose/tasks/main.yml`) les génère sur place avec
`community.crypto.openssl_privatekey`/`openssl_publickey`, de façon
idempotente. Les deux autres familles exigent qu'un opérateur lance les
scripts à la main sur la machine neuve avant que le mesh gRPC et l'entrée
TLS fonctionnent.

*(Scénario 1.2 : ne s'applique pas — les certificats survivent si l'instance
survit.)*
*(Scénario 1.3 : s'applique en totalité si les certificats/clés
eux-mêmes font partie du secret compromis.)*

### 3.5 Redéployer les services applicatifs

```bash
# /opt/sgfe/.env porte IMAGE_TAG et GHCR_REPO (voir DEPLOYMENT.md)
docker compose -f docker-compose.yml -f docker-compose.prod.yml pull
docker compose -f docker-compose.yml -f docker-compose.prod.yml run --rm auth-service \
  python manage.py migrate   # migrations en étape séparée, jamais au démarrage en prod
docker compose -f docker-compose.yml -f docker-compose.prod.yml up -d --no-build
```

Le pipeline `.github/workflows/cd-prod.yml` **existe et exécute exactement
cette séquence** (vérifié — OIDC, `cosign verify`, SSM `send-command`,
migrations en étape séparée, test de fumée GraphQL, rollback par variable),
mais **ne peut pas encore s'exécuter** : son job `prealables` échoue tant que
`secrets.AWS_DEPLOY_ROLE_ARN` et `vars.GHCR_REPO` ne sont pas configurés côté
GitHub — ce qui, à ce jour, n'a apparemment jamais été fait (cohérent avec
l'absence de toute trace d'une instance réellement provisionnée). En
attendant, cette étape se joue à la main, via SSM, par un opérateur.

*(Scénario 1.3 : redéploiement nécessaire pour propager les secrets
tournés, sans passer par 3.1-3.4.)*

### 3.6 Vérifications post-reconstruction

- `docker compose ps --format json` : les 21 conteneurs `healthy`.
- Test de fumée : requête GraphQL authentifiée (le même que `cd-prod.yml`
  prévoit).
- `FRONTEND_URL` bien positionnée dans l'environnement de production — sans
  elle, `docker compose config` échoue explicitement (`${FRONTEND_URL:?…}`,
  `DEPLOYMENT.md`), ce qui est le comportement voulu plutôt qu'un lien mort
  envoyé aux abonnés.
- DNS/IP publique effectivement repointés vers la nouvelle instance (§3.1).
- Un premier passage manuel de `scripts/backup-databases.sh` pour vérifier
  que la chaîne de sauvegarde (et son expédition hors instance, une fois
  câblée) fonctionne à nouveau avant de considérer l'incident clos.

---

## 4. Limites actuelles honnêtes

Un plan de DR qui masque ses propres limites est pire que pas de plan. Liste
non édulcorée, chaque ligne vérifiée dans le code ou la documentation
existante :

| Limite | État vérifié |
|---|---|
| **SPOF total** | Une seule instance EC2, une seule zone de disponibilité. « Choix assumé » (`INFRASTRUCTURE_AWS.md` §11.4) — pas un oubli, mais tant que ce choix tient, ce document en est la conséquence directe. |
| **Sauvegardes qui meurent avec l'instance** | Confirmé §2.1 : bind-mount local, bucket S3 créé par Ansible mais jamais alimenté par `backup-databases.sh`. RPO infini pour une perte totale d'instance, à ce jour. |
| **Pas de promotion automatique PostgreSQL** | La PoC de réplication (`postgres/replication/README.md`) le dit explicitement : « Pas de promotion automatique. […] Un outil comme Patroni, repmgr ou pg_auto_failover est nécessaire […] — aucun n'est en place ici. » Et la réplique vit sur la **même instance unique** que le primaire : elle ne protège pas contre le scénario 1.1, seulement contre une corruption d'un conteneur Postgres isolé pendant que la machine reste debout. |
| **Sentinel Redis fonctionne, mais aucun service ne le sait** | Bascule automatique réelle et testée (`redis/README.md`), mais tous les clients (8 services Django + whatsapp-service) se connectent à `redis:6379` en dur, jamais via un client Sentinel-aware. Après une bascule, `redis` continue de désigner l'ancien maître devenu réplique en lecture seule — la HA de Sentinel est invisible pour l'application. Même limite qu'au-dessus : la réplique vit sur la même instance unique. |
| **Session WhatsApp non automatisable** | `whatsapp-web.js` pilote un vrai navigateur derrière une vraie session téléphone (« un animal de compagnie », `INFRASTRUCTURE_AWS.md` §11.2). Un humain avec le téléphone dédié est requis, systématiquement. |
| **Aucun rattrapage des messages manqués** | Vérifié dans `services/notification/notifications/services.py` et confirmé par `services/notification/CLAUDE.md` : un `Envoi` en échec pendant une coupure WhatsApp reste `ECHEC`, définitivement, sauf renvoi manuel. |
| **Régénération des certificats non automatisée** | Seules les clés JWT RS256 sont générées par Ansible (`roles/compose`). Les certificats mTLS gRPC et TLS nginx/Let's Encrypt exigent une intervention manuelle sur la machine neuve. |
| **Pas d'IP/DNS automatiquement repointés** | Aucune Elastic IP ni entrée Route 53 dans `ansible/01-infra.yml` — à faire à la main après reconstruction. |
| **Chaîne de déploiement automatique non opérationnelle** | `cd-prod.yml` existe et est correctement conçu, mais bloqué faute des secrets GitHub `AWS_DEPLOY_ROLE_ARN`/`GHCR_REPO`. Rien n'indique que l'infrastructure décrite par Ansible ait déjà été provisionnée pour de vrai. |
| **`test-restore.sh` ne teste qu'une base** | Par défaut `config_db`, la plus petite des 8 — un « restore drill » **partiel**, pas une preuve que les 7 autres bases se restaurent aussi proprement, et pas un test de reconstruction complète. |
| **Une seule passphrase de chiffrement, sans rotation** | `BACKUP_ENCRYPTION_KEY` protège tous les dumps depuis toujours en cas de compromission ; aucun mécanisme de rotation observé dans le dépôt. |
| **Aucun chiffre de ce document n'est mesuré à l'échelle de production** | Le dépôt ne contient, au moment de la rédaction, que des données de démonstration (~19 abonnés). Les tailles de dump et durées de restauration citées en §2.2 datent de cette échelle et ne se projettent pas linéairement. |

---

## 5. Test du plan

Un plan de DR non testé n'est qu'une hypothèse. `scripts/test-restore.sh`
existe déjà et vaut la peine d'être utilisé — mais il faut être clair sur ce
qu'il couvre : **un test partiel du DR, pas le DR complet.** Il vérifie
qu'**une** sauvegarde chiffrée se déchiffre et se restaure sur un Postgres
jetable ; il ne provisionne aucune instance, ne restaure pas les 8 bases, ne
touche ni aux certificats ni à la session WhatsApp, et ne mesure aucun RTO
de bout en bout.

### 5.1 Cadence proposée

**Semestrielle.** Le rythme du projet (petite équipe, infrastructure encore
en construction — cf. `cd-prod.yml` non opérationnel) ne justifie pas plus
fréquent aujourd'hui ; mais un semestre sans exercice sur un système qui
évolue vite (8 bases, certificats gitignorés, playbooks encore jamais
rejoués pour de vrai) laisse le temps à ce document de devenir faux sans que
personne ne le remarque. À resserrer à trimestriel une fois l'instance de
production réellement en service.

### 5.2 Ce qu'un test minimal couvrirait

Un exercice qui mérite le nom de « test du DR » va au-delà de
`test-restore.sh` :

1. **Provisionner une instance jetable** avec `ansible/01-infra.yml` +
   `02-bootstrap.yml` contre un compte AWS de test — et **chronométrer**. Ce
   chiffre remplace directement les estimations non mesurées du §2.2.
2. **Étendre `test-restore.sh` aux 8 bases**, pas seulement `config_db` — le
   script accepte déjà un nom de base en paramètre, il suffit de le boucler.
   Mesurer la taille et le temps réel pour chacune, à l'échelle de données
   du moment (démo aujourd'hui, production demain).
3. **Vérifier le pull + la vérification de signature** des images depuis
   GHCR sur la machine neuve (`cosign verify` avant tout `up`).
4. **Régénérer les certificats** (mTLS gRPC + TLS) avec les scripts dédiés
   sur la machine neuve, et confirmer que les 9 composants gRPC et le nginx
   démarrent avec.
5. **Exclure délibérément** le rescan QR WhatsApp de l'automatisation — mais
   inclure sa procédure écrite (§3.3) dans la checklist humaine du test, pour
   vérifier qu'elle reste exacte.
6. **Consigner le temps total** et mettre à jour §2.2 avec le chiffre
   mesuré plutôt que l'estimation.

### 5.3 Prérequis à traiter avant le premier test réel

Faire tourner ce test aujourd'hui contre le scénario 1.1 (perte totale de
l'instance) serait un exercice vide tant que le §2.1 n'est pas résolu :
sans expédition des sauvegardes hors instance, il n'y a rien à restaurer sur
la machine jetable. **Câbler `backup-databases.sh` vers
`sgfe-sauvegardes-*` est donc un prérequis au premier test crédible du
scénario le plus sévère de ce document — pas une amélioration à faire
« plus tard ».**
