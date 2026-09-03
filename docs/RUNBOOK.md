# Runbook d'exploitation — SGFE Backend

> **Nature de ce document :** que faire quand quelque chose ne va pas — incidents,
> restauration, rollback. Complète `docs/DEPLOYMENT.md` (comment déployer normalement)
> et `docs/CHAINE_DE_LIVRAISON.md` (qui déploie quoi, dans quel ordre). Pour le
> dimensionnement et les coûts, voir `docs/INFRASTRUCTURE_AWS.md` ; pour la liste des
> composants réels, voir `docs/ETAT_DU_SYSTEME.md`.
> **État constaté le :** 2026-09-03, sur `develop`. Certaines procédures ci-dessous
> (sauvegardes chiffrées, TLS nginx, réplication PostgreSQL/Redis) référencent du code
> qui existe dans le dépôt mais vit encore sur des branches non fusionnées au moment de
> la rédaction — chaque section le signale explicitement et donne la commande pour
> vérifier soi-même l'état réel avant d'agir (`ls scripts/…`, `git log`).
> **À maintenir :** quand une procédure décrite ici cesse de correspondre au code
> (message d'erreur changé, script renommé, service ajouté), corriger cette page dans
> la même PR que le changement.

---

## Table des matières

1. [Diagnostic rapide — sans observabilité dédiée](#1-diagnostic-rapide--sans-observabilité-dédiée)
2. [Incidents par symptôme](#2-incidents-par-symptôme)
3. [Procédure de restauration après perte de données](#3-procédure-de-restauration-après-perte-de-données)
4. [Rollback applicatif](#4-rollback-applicatif)
5. [Rollback de migration](#5-rollback-de-migration)
6. [Contacts et escalade](#6-contacts-et-escalade)

---

## 1. Diagnostic rapide — sans observabilité dédiée

**Rappel honnête** (voir CLAUDE.md racine, §Observabilité) : il n'y a ni `/metrics`
Prometheus, ni traces OpenTelemetry, ni tableau de bord de supervision aujourd'hui.
Les deux seuls outils réels sont `docker compose ps` et `docker compose logs`. Ce
runbook part de cette contrainte plutôt que de la maquiller.

### 1.1 État des 21 conteneurs

```sh
docker compose ps --format json
```

Regarder trois champs par service : `State` (`running` sinon problème), `Health`
(`healthy` / `unhealthy`) et `Status` (`Up 6 days (healthy)` vs `Exited (1) …`).
En pratique, `Health` est renseigné pour les **21 conteneurs** : Postgres et Redis
via le `healthcheck:` déclaré au niveau `docker-compose.yml` (9 occurrences —
`grep -c healthcheck: docker-compose.yml`), les 8 services Django + gateway + nginx
+ whatsapp-service (11 au total) via le `HEALTHCHECK` de leurs Dockerfiles (appliqué
par Docker même sans déclaration compose). La distinction compte pour autre chose :
seul le `healthcheck:` déclaré au niveau compose peut être référencé par un
`depends_on: condition: service_healthy` d'un autre service — c'est pour ça que
seuls Postgres/Redis en portent un ici, pas parce que les autres seraient sans
surveillance.

Version lisible sans JSON :

```sh
docker compose ps
```

### 1.2 Logs des 50 dernières lignes, tous services

```sh
for s in $(docker compose config --services); do
  echo "== $s =="
  docker compose logs --tail=50 "$s"
done
```

Pour un seul service en continu : `docker compose logs -f <service> --tail=50`.

### 1.3 Sonde nginx (l'unique point d'entrée publié)

```sh
curl -f http://localhost:8080/healthz
```

`nginx/default.conf` répond `200 OK` **localement**, sans jamais interroger la
gateway — ça confirme que nginx tourne, pas que la chaîne applicative répond. Pour
vérifier la chaîne complète, passer par une requête GraphQL réelle ou vérifier que
`gateway` est `healthy` dans `docker compose ps`.

### 1.4 Sonde whatsapp-service

```sh
curl http://localhost:3000/health
```

Réponse JSON (`server.js`, endpoint public, pas de clé requise) :
`{"ready": true|false, "phase": "connecte|qr|rupture|demarrage|deconnecte", "depuis": <ms|null>}`,
HTTP 200 si `ready:true`, **503 sinon**. Voir §2.3 pour l'action à mener selon `phase`.

### 1.5 Ce que l'absence de `restart:` implique aujourd'hui

Sur `docker-compose.yml` de base (celui qui tourne réellement en local et tant que
`docker-compose.prod.yml` n'est pas superposé), **seuls `whatsapp-service` et
`db-backup` portent `restart: unless-stopped`**. Les 8 services Django, la gateway et
nginx n'en ont pas : un crash isolé (OOM, exception non catchée qui fait sortir le
process) laisse le conteneur **`Exited`, silencieusement, jusqu'à ce qu'un humain le
remarque** — `docker compose ps` ne l'affiche plus en train de tourner mais rien
n'alerte personne. Le premier réflexe d'un diagnostic à 3h du matin doit donc être
`docker compose ps` en entier, pas seulement les services qu'on soupçonne.

> Une branche en cours (`fix/healthchecks-microservices`, non fusionnée sur `develop`
> au moment de la rédaction) doit combler ce manque. Vérifier avant d'agir :
> `grep -c "restart: unless-stopped" docker-compose.yml` (2 aujourd'hui — whatsapp-service
> et db-backup uniquement ; un chiffre plus élevé signifie que la branche a fusionné).

---

## 2. Incidents par symptôme

### 2.1 Un service ne démarre pas / boucle de redémarrage

Trois causes fail-fast connues et **codées** dans ce dépôt — chacune produit un
message précis à chercher dans `docker compose logs <service>`.

#### a) `auth-service` — clés JWT RS256 absentes

```
django.core.exceptions.ImproperlyConfigured: Clé JWT introuvable (<chemin>).
Générez la paire RSA depuis la racine du backend : ./scripts/gen-jwt-keys.sh
```

Source : `services/auth/auth/settings.py` (bloc qui lit `services/auth/keys/*.pem`).
**Fix :**

```sh
./scripts/gen-jwt-keys.sh
docker compose up -d auth-service
```

#### b) N'importe quel service (les 9 composants gRPC) — `INTERNAL_GRPC_KEY` absente

```
CleInterneManquante: AuthServerInterceptor : INTERNAL_GRPC_KEY absente ou vide.
Le service refuse de démarrer sans clé d'authentification interne. Définissez-la
dans l'environnement (y compris en local) avant de lancer le service.
```

Source : `<app>/grpc_auth.py::exiger_cle()` (fichier identique dans les 9 services,
ex. `services/campagne/campagnes/grpc_auth.py:64-77`), levée à la construction de
`AuthServerInterceptor` dans `grpc_server.py::serve()`. **Fail-closed volontaire** —
voir la docstring du module : pas de repli silencieux comme pour mTLS (point d).
**Fix :** vérifier que `INTERNAL_GRPC_KEY` est bien définie dans l'environnement du
conteneur (`docker compose exec <service> env | grep INTERNAL_GRPC_KEY` — en pratique
quasi impossible à déclencher via `docker compose up` normal puisque le compose de
base fournit un défaut `${INTERNAL_GRPC_KEY:-grpc-dev-placeholder-do-not-use-in-production}`
: si ça arrive, c'est qu'un `.env` de prod a explicitement posé une valeur vide).

#### c) `whatsapp-service` — `WHATSAPP_INTERNAL_API_KEY` absente

```
[WhatsApp] FATAL : WHATSAPP_INTERNAL_API_KEY absente ou vide. Le service refuse
de démarrer sans clé d'authentification interne. Définissez-la dans l'environnement
(y compris en local) avant de lancer le service.
```

Source : `whatsapp-service/server.js` (avant tout `app.listen`), suivi de
`process.exit(1)`. Même remède que (b).

#### d) mTLS gRPC (`GRPC_TLS_CA` / `GRPC_TLS_CERT` / `GRPC_TLS_KEY`) — **jamais de fail-fast**

Contrairement à (a)-(c), l'absence ou l'illisibilité de ces trois variables ne fait
**pas** planter le service : `<app>/grpc_auth.py::_lire_credentiel_tls()` retombe
silencieusement sur `add_insecure_port`/`insecure_channel` (repli en clair). Le seul
signal, et seulement si la variable est définie mais le fichier illisible, est un
`logger.warning` :

```
GRPC_TLS_CERT défini (/app/certs/server.crt) mais illisible (<erreur>) —
repli sur gRPC en clair.
```

**Si les 3 variables sont absentes**, aucun log n'apparaît du tout — c'est le
comportement normal des suites de tests, mais **pas souhaitable en production**.
Pour vérifier que mTLS est réellement actif entre les services :

```sh
docker compose exec auth-service env | grep GRPC_TLS
ls -la certs/           # doit contenir ca.crt, ca.key, server.crt, server.key
```

Si `certs/` est vide ou absent : générer la CA + le certificat partagé (mêmes
noms d'hôte que les 9 composants gRPC, SAN incluse) :

```sh
./scripts/generate-grpc-certs.sh      # -> certs/, gitignoré
docker compose up -d                  # remonter les services pour remonter les volumes
```

#### Note sur `restart:` en développement vs production

Sur le compose de base (dev), un service qui échoue à ces fail-fast **reste
`Exited`** (voir §1.5) — il faut relancer `docker compose up -d <service>` après
correction. Sur `docker-compose.prod.yml`, `x-prod: &prod` pose
`restart: unless-stopped` : un secret qui manque en production **fait boucler le
conteneur indéfiniment** (`Restarting` en continu dans `docker compose ps`) plutôt
que de rester arrêté — c'est un signal différent à reconnaître.

---

### 2.2 La base de données d'un service est inaccessible

Aucun fail-fast applicatif custom ici : les variables `<SVC>_DB_HOST/PORT/NAME/USER/PASSWORD`
sont lues avec un simple défaut (`env("CAMPAGNE_DB_HOST", default="localhost")`, etc.).
Une base injoignable au démarrage fait échouer `python manage.py migrate` (première
moitié du `command:` de chaque service) avec une **exception Django brute**, pas un
message applicatif :

```
django.db.utils.OperationalError: connection to server at "<host>-postgres" (…),
port 5432 failed: Connection refused
```

ou, host mal résolu :

```
django.db.utils.OperationalError: could not translate host name "<host>" to address
```

**Diagnostic :**

```sh
docker compose ps <service>-postgres --format json   # Health: unhealthy/starting/absent ?
docker compose logs <service>-postgres --tail=50      # erreurs Postgres lui-même
```

**Causes courantes et remèdes :**

- **Le conteneur Postgres est down/unhealthy** — `docker compose up -d <service>-postgres`,
  puis surveiller `pg_isready` via le healthcheck (`docker compose ps` repasse à
  `healthy` sous 5-50s selon `interval`/`retries` déclarés dans `docker-compose.yml`).
- **Volume plein** — `docker compose logs <service>-postgres` montrant
  `No space left on device` → vérifier l'espace disque hôte (`df -h`), pertinent
  surtout sur la cible EC2 `t4g.medium` (30 Go gp3, voir `INFRASTRUCTURE_AWS.md` §9).
- **Identifiants désynchronisés** après une rotation de secret — comparer la valeur
  effective du conteneur (`docker compose exec <service> env | grep DB_PASSWORD`)
  avec celle de `<service>-postgres` (`POSTGRES_PASSWORD`).
- **Le service Django lui-même reste `Exited`** parce que `migrate` a échoué avant
  `grpc_server` — une fois Postgres réparé, il faut relancer explicitement le
  service applicatif (pas seulement sa base) : `docker compose up -d <service>`.

**Pas de bascule automatique.** Une preuve de concept de réplication
PostgreSQL/Redis existe (`postgres/replication/`, `redis/`, branche
`feat/resilience-securite-infra` — non fusionnée sur `develop` au moment de la
rédaction, vérifier avec `ls postgres/replication/ 2>/dev/null`), mais elle n'est
**câblée à aucun service applicatif** : chaque service pointe sur un nom d'hôte fixe
(`<SVC>_DB_HOST=<svc>-postgres`), et même avec une réplique qui existerait, il n'y a
ni promotion automatique ni redécouverte côté client. **Une base réellement perdue
(pas juste temporairement down) se traite par restauration — voir §3, pas par
bascule.**

---

### 2.3 `whatsapp-service` déconnecté

**Symptôme observable** : les factures/OTP/relances WhatsApp ne partent plus ;
`notification-service`/`auth-service` loguent des échecs de `whatsapp_client.py`
(`statut: ECHEC`, `"WhatsApp non connecté"`).

**Diagnostic :**

```sh
curl http://localhost:3000/health
```

`{"ready": false, "phase": "qr", ...}` ou `phase: "deconnecte"` → session perdue,
action humaine requise. `phase: "rupture"` ou `"demarrage"` → transitoire, le service
tente de se reconnecter tout seul (voir plus bas).

**Ne pas faire de réflexe `docker compose restart whatsapp-service`.** La session
WhatsApp (`RemoteAuth`) est persistée dans Redis, pas dans le conteneur ; un
`disconnected`/`auth_failure` déclenche déjà un redémarrage interne du client avec
backoff exponentiel (plafonné à 60s, `server.js`), et un watchdog relance
l'initialisation si elle reste bloquée sans jamais produire de QR ni de `ready`.
Redémarrer le conteneur ne répare rien de plus quand la session est **réellement**
perdue (il n'y a alors qu'un rescan qui la répare), et interromprait un backoff en
cours dans le cas contraire.

**Relancer une session (rescan du QR) :**

- **Voie normale** : passer par la Gateway (query GraphQL `whatsappQr`, réservée
  ADMIN, qui relaie le QR sans exposer la clé interne du whatsapp-service — voir
  `docs/ETAT_DU_SYSTEME.md` §5.1) ou la subscription `whatsappStatus` qui pousse le
  QR sans polling, depuis le back-office.
- **Voie infra directe** (dépannage bas niveau, nécessite `WHATSAPP_INTERNAL_API_KEY`) :

  ```sh
  curl -H "X-Internal-Api-Key: $WHATSAPP_INTERNAL_API_KEY" \
       http://localhost:3000/qr-data   # JSON {ready, qr (data-URL PNG), number, phase, depuis}
  ```

  Décoder le champ `qr` (data URL PNG) et scanner avec WhatsApp → Appareils connectés.

**Rappel de sécurité pour ce runbook** : ne jamais déclencher un envoi WhatsApp réel
pour « tester » — `curl` sur `/health` et `/qr-data` est en lecture seule, `/send` et
`/send-with-pdf` ne le sont pas.

---

### 2.4 Un job planifié (APScheduler) semble ne plus tourner

Quatre jobs, tous démarrés **dans le process du serveur gRPC** (`start_scheduler()`
appelé par `management/commands/grpc_server.py` de chaque service — pas de conteneur
dédié) :

| Service | Fichier | Cron (UTC) | Log de démarrage (une fois, au boot) | Log de fin de run |
|---|---|---|---|---|
| campagne | `campagnes/schedulers.py` | 07:00 (`campagne_planifiee`) | `"CampagneScheduler démarré — cron à 07:00 tous les jours, retry facturation toutes les heures."` | `"Campagne démarrée automatiquement"` ou `"Aucune campagne planifiée à démarrer aujourd'hui."` |
| campagne | `campagnes/schedulers.py` | toutes les heures, `:00` (`facturation_retry`) | *(même log que ci-dessus)* | `"Génération de factures réussie après nouvelle tentative"` / `"Régénération de facture réussie…"` / silence si rien en attente |
| paiement | `paiements/schedulers.py` | 08:00 (`impaye_checker`) | `"PaiementScheduler démarré — cron à 08:00 tous les jours."` | `"ImpayeCheckerJob terminé avec succès."` ou `"ImpayeCheckerJob échoué : %s"` |
| reporting | `stats/schedulers.py` | 03:00 (`reporting_reconciliation`) | `"ReportingScheduler démarré — réconciliation nocturne à 03:00."` | `"ReconciliationJob terminé : %s campagne(s) réconciliée(s), %s échec(s)."` |
| notification | `notifications/schedulers.py` | toutes les 15s (`diffusion_processor`) | `"NotificationScheduler démarré — diffusions traitées toutes les 15s."` | rien si aucune diffusion en attente ; `logger.exception` seulement en cas d'échec |

**Vérifier qu'un job tourne :**

```sh
docker compose logs <service> | grep "Scheduler démarré"          # une fois au boot
docker compose logs <service> --since 24h | grep -E \
  "Campagne démarrée|Aucune campagne planifiée|ImpayeCheckerJob|ReconciliationJob"
```

Chaque job (sauf `diffusion_processor`, toutes les 15s) pose un `misfire_grace_time`
(6h pour `campagne_planifiee`/`impaye_checker`/`reporting_reconciliation`, 30 min
pour `facturation_retry`) et `coalesce=True` : un conteneur redémarré peu après
l'heure prévue rattrape le passage manqué **une seule fois**, pas en boucle. Passé ce
délai de grâce, **rien ne rattrape le passage manqué** — seule l'absence du log de
fin de run à l'heure prévue (+ fenêtre de grâce) le révèle.

> **Incident déjà documenté dans le code lui-même**
> (`campagnes/schedulers.py:142-152`) : un passage manqué de `campagne_planifiee_job`
> a laissé **6 factures à 31 jours de retard et zéro `SuiviImpaye`** créé — aucune
> relance partie, aucun abonné suspendu — sans qu'aucune erreur n'apparaisse ailleurs
> que l'absence du log APScheduler. C'est le symptôme exact à chercher : pas une
> erreur, une **absence** de ligne de log à l'heure attendue.

Chaque job pose aussi un verrou consultatif PostgreSQL (`pg_try_advisory_lock`,
clés `4210001`-`4210004` selon le service) pour éviter un double déclenchement en cas
de réplication future — sans effet aujourd'hui (une seule instance par service), mais
si le log montre `"… ignoré — verrou détenu par une autre instance."` de façon
persistante, c'est qu'un verrou est resté posé après un crash : redémarrer le service
libère la connexion PostgreSQL qui le tenait.

#### Cas particulier : `reporting-service` n'agrège plus les stats (pas un job APScheduler — le consumer d'événements)

Le tableau de bord est alimenté par **deux mécanismes distincts** dans
`reporting-service` : la réconciliation nocturne ci-dessus (source de vérité,
recalculée depuis Facturation/Paiement chaque nuit) et un **consumer Redis Streams
toujours actif** (`stats/event_consumer.py`, thread démarré au boot par
`start_consumer_thread()`) qui applique les deltas en continu.

**Symptôme** : les stats de facturation/paiement n'avancent plus en direct (le
dashboard ne bouge qu'après le passage de 03:00) et les logs montrent en boucle :

```
Traitement de l'événement <id> échoué (sera redélivré)
```

**Cause** : `_handle_entries()` (`event_consumer.py:92-101`) attrape toute exception
sans jamais faire `XACK` — l'entrée reste dans la Pending Entries List (PEL) du
consumer group et sera **rejouée à chaque redémarrage du service** (le rattrapage au
boot relit tout le non-acquitté via `xreadgroup(..., "0")`). Une cause réelle
observée : un événement `PAIEMENT_STATS` avec un `campagne_id` vide, qui casse la
validation UUID de Django (`update_stats_paiements` → `StatsPaiements.objects.get_or_create`).

**Diagnostic :**

```sh
docker compose logs reporting-service | grep -c "sera redélivré"     # nombre d'échecs
docker compose exec redis redis-cli XPENDING reporting:stream reporting-consumers
#  <compte> <id-le-plus-ancien> <id-le-plus-récent> <consumer> <compte>
docker compose exec redis redis-cli XRANGE reporting:stream <id> <id>   # payload exact
```

**Remède :**

1. Inspecter chaque message en attente avec `XRANGE` pour confirmer qu'il s'agit
   bien d'un événement malformé (champ manquant/vide) et non d'une panne transitoire.
2. Le read model reporting est **best-effort, jamais source de vérité** (Facturation
   et Paiement le restent) et la réconciliation nocturne de 03:00 recalcule
   `StatsFacturation`/`StatsPaiements` depuis ces deux services — acquitter un
   message confirmé irrécupérable est donc sans risque de corruption durable :
   ```sh
   docker compose exec redis redis-cli XACK reporting:stream reporting-consumers <id>
   ```
   À faire message par message après inspection, jamais en boucle aveugle sur tout
   `XPENDING`.
3. Escalader côté producteur : la trace pointe `update_stats_paiements`, donc
   `paiement-service` — c'est lui qui a publié un événement avec `campagne_id` vide
   à corriger pour que le problème ne se reproduise pas.

---

### 2.5 Conflit de migration après un merge

Deux branches qui ajoutent chacune la migration suivante d'une même app (ex. deux PR
qui créent chacune `factures/migrations/0006_xxx.py` à partir de `0005`) produisent,
au premier `migrate` après le merge :

```
CommandError: Conflicting migrations detected; multiple leaf nodes in the migration
graph: (0006_a, 0006_b in factures).
To fix them run 'python manage.py makemigrations --merge'
```

**Fix (app_label ≠ nom du dossier service — voir la table ci-dessous) :**

```sh
docker compose exec <service> python manage.py showmigrations <app_label>
docker compose exec <service> python manage.py makemigrations --merge <app_label>
# relit les deux migrations en tête de graphe, génère
# <app_label>/migrations/00XX_merge_<a>_<b>.py qui déclare les deux comme dépendances
docker compose exec <service> python manage.py migrate <app_label>
```

Une migration de fusion **n'est pas un squash** — CLAUDE.md racine interdit
`squashmigrations` en dev, pas la génération d'une migration de merge, qui s'ajoute à
l'historique au lieu de le réécrire. La committer normalement.

**Cas plus délicat** — les deux migrations touchent le **même champ** de façon
incompatible (ex. deux types différents ajoutés à la même colonne) : `--merge` seul
ne suffit pas, Django le signalera à l'application (erreur de colonne dupliquée ou de
contrainte). Il faut alors éditer manuellement l'une des deux migrations avant de
relancer `--merge` — pas de raccourci automatique pour ce cas.

**Table de correspondance dossier service → `app_label` Django** (nécessaire car ils
diffèrent) :

| Dossier | `app_label` |
|---|---|
| `services/campagne` | `campagnes` |
| `services/facturation` | `factures` |
| `services/paiement` | `paiements` |
| `services/abonne` | `abonnes` |
| `services/notification` | `notifications` |
| `services/config` | `parametres` |
| `services/reporting` | `stats` |
| `services/auth` | `comptes` |

---

## 3. Procédure de restauration après perte de données

Le service `db-backup` (`docker-compose.yml`) exécute `scripts/backup-databases.sh`
une fois au démarrage puis toutes les 24h, `pg_dump` gzip des 8 bases dans
`./backups/`. **Le format exact dépend de la version du script réellement présente
sur la machine** — vérifier avant d'agir :

```sh
grep -c BACKUP_ENCRYPTION_KEY scripts/backup-databases.sh
```

- **0** (état de `develop` au 2026-09-03) : dumps en clair, `backups/<db>_<horodatage>.sql.gz`.
- **≥1** (branche `fix/hardening-infra-secrets`, pas encore fusionnée) : dumps
  chiffrés AES-256-CBC/PBKDF2, `backups/<db>_<horodatage>.sql.gz.enc`, et
  `scripts/test-restore.sh` existe. Vérifier : `ls scripts/test-restore.sh`.

### 3.1 Si `scripts/test-restore.sh` existe — utiliser le drill automatisé

```sh
BACKUP_ENCRYPTION_KEY=<passphrase> ./scripts/test-restore.sh [nom_base] [dossier_backups]
# nom_base : défaut config_db (la plus petite des 8) ; dossier_backups : défaut ./backups
```

Ce script (lu intégralement avant rédaction de cette section) :

1. sélectionne le dump `.sql.gz.enc` le plus récent pour `nom_base` ;
2. le déchiffre + décompresse dans un fichier temporaire ;
3. démarre un conteneur Postgres **jetable** (`sgfe-restore-drill-$$`, jamais un
   conteneur ou une base existants) ;
4. crée les rôles référencés par le dump (`OWNER TO`/`GRANT … TO`, ex. `config_user`)
   à la volée, puisqu'ils n'existent pas sur ce Postgres jetable ;
5. restaure le dump, vérifie qu'au moins une table existe et affiche le compte de
   lignes par table ;
6. détruit le conteneur temporaire en fin de script, succès ou échec (`trap … EXIT`).

C'est un **test de restaurabilité**, pas une restauration réelle — il ne touche
jamais aux 8 bases de service. À exécuter régulièrement (pas seulement en incident)
pour s'assurer que les sauvegardes sont réellement exploitables, pas seulement
produites.

### 3.2 Restauration réelle dans une base de service (perte de données confirmée)

```sh
# 1. Arrêter le service applicatif concerné pour éviter des écritures pendant la restauration
docker compose stop <service>

# 2. Identifier le dernier dump
ls -t backups/ | grep <db>_ | head -5

# 3a. Cas chiffré (.sql.gz.enc) :
openssl enc -d -aes-256-cbc -pbkdf2 -salt -pass env:BACKUP_ENCRYPTION_KEY \
    -in backups/<db>_<horodatage>.sql.gz.enc | gunzip -c \
    | docker compose exec -T <db>-postgres psql -U <db>_user -d <db>

# 3b. Cas non chiffré (.sql.gz) :
gunzip -c backups/<db>_<horodatage>.sql.gz \
    | docker compose exec -T <db>-postgres psql -U <db>_user -d <db>

# 4. Vérifier avant de redémarrer le service
docker compose exec <db>-postgres psql -U <db>_user -d <db> -c "\dt"
docker compose exec <db>-postgres psql -U <db>_user -d <db> -c \
  "SELECT count(*) FROM <table_clé_du_service>;"   # ex. abonnes_abonne, factures_facture…

# 5. Redémarrer et surveiller le boot (migrate doit dire "No migrations to apply" si
#    le dump correspond au schéma courant, sinon une divergence de schéma apparaîtra ici)
docker compose up -d <service>
docker compose logs -f <service> --tail=50
```

**Prérequis à ne jamais perdre de vue** : `BACKUP_ENCRYPTION_KEY` (si le script
chiffré est en place) doit être conservée **hors** de la machine qui héberge les
sauvegardes — une passphrase perdue rend tous les dumps chiffrés définitivement
inexploitables. `docs/CHAINE_DE_LIVRAISON.md` §13.3 rappelle que ces dumps vivent
aujourd'hui sur le même disque que les bases qu'ils protègent (`./backups/`) : une
perte d'instance complète emporte les deux — c'est le seul scénario de ce dépôt où la
panne est totale et irréversible tant que les dumps ne sont pas expédiés hors machine
(S3, non encore en place).

---

## 4. Rollback applicatif

Mécanisme réel, vérifié dans `docker-compose.prod.yml` : chaque service pointe sur
`ghcr.io/${GHCR_REPO:?…}/<service>:${IMAGE_TAG:?…}`, où `IMAGE_TAG` vit dans
`/opt/sgfe/.env`. **Déployer = réécrire ce tag et tirer. Revenir en arrière =
réécrire l'ancien tag.** Aucune reconstruction dans les deux sens.

> Aucune production n'est encore en ligne à ce jour — `cd-prod.yml`
> (`docs/CHAINE_DE_LIVRAISON.md` §9) ne peut pas encore s'exécuter (secrets AWS non
> configurés). La procédure ci-dessous décrit le mécanisme déjà codé et prêt, pas une
> opération déjà exercée en production réelle.

### 4.1 Sur une machine avec `docker-compose.prod.yml` (production, dès qu'elle existe)

```sh
cd /opt/sgfe
cat .env.precedent                      # confirmer le tag vers lequel revenir
cp .env .env.echec-$(date +%Y%m%d-%H%M%S)   # garder une trace du tag fautif
cp .env.precedent .env
docker compose -f docker-compose.yml -f docker-compose.prod.yml pull
docker compose -f docker-compose.yml -f docker-compose.prod.yml up -d --no-build
docker compose -f docker-compose.yml -f docker-compose.prod.yml ps --format json   # tout healthy ?
```

`--no-build` n'est pas décoratif : sans lui, un `IMAGE_TAG` qui ne correspond à
aucune image publiée déclencherait une reconstruction locale silencieuse au lieu
d'échouer proprement (voir `docs/DEPLOYMENT.md` §« --no-build n'est pas décoratif »).

### 4.2 En local / dev (compose de base uniquement, pas d'`IMAGE_TAG`)

Il n'y a pas d'image à re-pointer : le compose de base construit depuis les sources
(`build:`). Revenir à la version précédente d'un service :

```sh
git log --oneline -- services/<service>/     # identifier le commit précédent
git checkout <sha_precedent> -- services/<service>/
docker compose up -d --build <service>
```

### 4.3 Ce que le rollback ne fait jamais

Revenir à l'image précédente **ne touche pas au schéma de base**. C'est volontaire
(`docs/CHAINE_DE_LIVRAISON.md` §9) : une migration Django n'est pas toujours
réversible, et l'inverser à l'aveugle est plus risqué que de laisser un schéma en
avance sur le code — d'où la règle « les migrations restent compatibles vers
l'arrière » (ajouter une colonne, jamais la renommer en une fois). Si le déploiement
fautif a réellement besoin d'un retour de schéma, voir §5.

---

## 5. Rollback de migration

CLAUDE.md racine interdit le squash de migrations en dev précisément pour que ce
type d'opération reste possible : chaque migration est un point de restauration
individuel.

### 5.1 Identifier la migration courante et la précédente

```sh
docker compose exec <service> python manage.py showmigrations <app_label>
```

Exemple réel vérifié sur ce dépôt (`campagne-service`, app `campagnes`) :

```
campagnes
 [X] 0005_releveaudit
 [X] 0006_releve_camp_releve_quartier_affectationzone
 [X] 0007_campagne_facturation_en_attente_and_more   <- dernière appliquée
```

### 5.2 Vérifier que la migration est réversible AVANT de reculer

```sh
cat services/<service>/<app_label>/migrations/0007_*.py
```

Chercher `RunPython` sans `reverse_code=`, ou `RunSQL` sans second argument (SQL de
réversion) — Django refusera de reculer avec une erreur explicite
(`IrreversibleError`) si l'une de ces opérations n'a pas de contrepartie inverse. Le
savoir avant d'essayer évite de démarrer une opération à moitié.

### 5.3 Reculer d'une migration

```sh
# Développement / staging (le service tourne déjà, migrate exécuté dans le conteneur vivant)
docker compose exec <service> python manage.py migrate <app_label> 0006_releve_camp_releve_quartier_affectationzone

# Production (docker-compose.prod.yml retire `migrate` du `command:` — voir DEPLOYMENT.md
# §Migrations — donc pas de conteneur vivant qui migre tout seul ; lancer un conteneur
# éphémère avec la MÊME image que celle en service, pas nécessairement la précédente) :
docker compose -f docker-compose.yml -f docker-compose.prod.yml run --rm \
  <service> python manage.py migrate <app_label> 0006_releve_camp_releve_quartier_affectationzone
```

### 5.4 Vérifier après coup

```sh
docker compose exec <service> python manage.py showmigrations <app_label>
docker compose logs <service> --tail=50   # le service redémarre-t-il proprement contre ce schéma ?
```

### 5.5 Ordre avec un rollback d'image (§4)

Si la migration en cause a été introduite **avec** un nouveau champ que le code
précédent ignore superficiellement, un rollback d'image seul (§4) suffit souvent —
c'est tout l'intérêt de la règle « migrations rétro-compatibles ». Ne reculer
réellement le schéma (cette section) que si la migration elle-même est l'élément
fautif du déploiement (elle a cassé un service à l'application, ou son effet doit
être défait avant que l'image précédente puisse fonctionner correctement contre la
base).

---

## 6. Contacts et escalade

**À définir — aucune astreinte formalisée à ce jour.** Il n'existe dans ce dépôt
aucun fichier de rotation d'astreinte, aucune intégration PagerDuty/Opsgenie, aucun
canal d'alerte configuré. En cas d'incident, le seul point de repère réel est
l'historique Git (`git log --oneline -- <fichier concerné>`, `git blame`) pour
identifier qui a écrit ou modifié le code en cause en dernier — ce n'est pas une
astreinte, seulement la meilleure information disponible aujourd'hui. Mettre en
place une astreinte formelle (qui, comment la joindre, quelle SLA) reste un chantier
ouvert, à traiter comme un point d'infrastructure à part entière plutôt qu'inventé
ici.
