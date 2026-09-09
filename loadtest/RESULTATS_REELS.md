# Résultats réels — k6 contre la stack Docker Compose vivante

**Date du run :** 9 septembre 2026, ~15h40–15h55 (heure locale, WAT/UTC+1).
**Auteur :** exécution manuelle demandée en tâche d'observation (pas d'écriture de fonctionnalité).

## Pourquoi ce document

`loadtest/README.md` documentait jusqu'ici deux choses vérifiées séparément
mais jamais ensemble : que les scripts k6 (`parcours-metier.js` ici,
`SGFE-frontend/loadtest/basic.js`) compilent et s'exécutent sans bug contre un
**mock GraphQL jetable**, et que personne n'avait encore lancé l'un ou
l'autre contre la **vraie stack Docker Compose**. Ce document comble ce vide :
les deux scripts ont été exécutés pour de vrai, contre la stack backend +
frontend réellement démarrée sur ce poste, avec un compte de démo réel et les
données réellement présentes en base à ce moment-là.

**Résultat en une phrase :** aucune erreur applicative (0 % d'échecs HTTP/GraphQL
dans les trois runs), mais des latences très supérieures aux seuils
documentés — et l'environnement dans lequel ce run a eu lieu n'était **pas**
isolé : preuve directe qu'au moins deux autres sessions travaillaient
activement sur ce même poste, dont une qui a redéployé cette même stack
backend pendant la fenêtre de test. Le détail et les nuances sont ci-dessous ;
ne pas citer les chiffres de latence de ce document comme une capacité de
production sans lire d'abord la section « Facteur de confusion ».

## Ce qui n'a PAS été fait, et pourquoi

- **`scripts/seed_demo.sh` n'a pas été relancé** (mémoire projet : données de
  démo volontairement purgées le 27 août 2026, ne pas régénérer sans
  autorisation explicite). Le test a tourné avec ce qui existait réellement
  en base au moment du run — voir « Jeu de données » plus bas.
- **Aucun fichier de config n'a été modifié** (`.env`, `docker-compose.yml`,
  certificats) — la stack était déjà démarrée par ailleurs (voir plus bas),
  et `.env` semble activement maintenu par un autre processus pendant cette
  session (horodatage très récent, voir « Anomalie `.env` »).
- **`docker compose up`/`down` n'a été exécuté par cette tâche à aucun
  moment** — ni pour démarrer, ni pour arrêter. Voir « État de la stack au
  démarrage » et « Arrêt (ou non) des stacks » plus bas.
- **Aucune mutation GraphQL n'a été exercée** — les deux scripts existants
  sont délibérément en lecture seule (rationnel documenté dans leur propre
  en-tête : aucune mutation de nettoyage sûre n'existe côté `abonne`). Voir
  « Ce que ces scripts ne mesurent toujours pas » pour ce que ça implique
  pour le scénario métier décrit dans la demande initiale (encaissement FIFO
  concurrent à une correction de relevé).

## État de la stack au démarrage — déjà vivante

Avant toute action de cette tâche, `docker ps` montrait déjà les deux piles
Compose (`sgfe-backend`, `sgfe-frontend`) démarrées et `healthy` depuis
**2 jours** pour l'essentiel des conteneurs applicatifs (`gateway`,
`auth-service`, `nginx`, `sgfe-frontend-frontend-1`) et depuis **46 heures**
pour les 7 autres services métier. Aucun `docker compose up` n'a donc été
nécessaire ni exécuté par cette tâche : lancer `--build` sur une stack déjà
vivante et déjà utilisée par ailleurs (voir plus bas) aurait été le genre de
sollicitation non coordonnée que `loadtest/README.md` demande justement
d'éviter.

Seule anomalie préexistante notée sans intervention : `sgfe-backend-whatsapp-service-1`
était `unhealthy` (Chromium/whatsapp-web.js) et `sgfe-backend-abonne-postgres-replica-1`
était sorti en erreur (`Exited (1)`) — aucun des deux n'est sur le chemin des
requêtes GraphQL exercées par ces scripts (login/abonnés/campagnes/factures/
dashboard/stats), donc sans impact sur ce qui suit.

Certificat nginx : déjà présent (`nginx/certs/fullchain.pem`, `privkey.pem`,
datés du 3 septembre), `./scripts/generate-nginx-cert.sh` n'a donc pas eu
besoin d'être relancé.

## Anomalie `.env` observée (non corrigée, hors périmètre)

`docker compose ps` depuis un shell de cette tâche échouait systématiquement :

```
error while interpolating services.auth-service.environment.PII_LOOKUP_HMAC_KEY:
required variable PII_LOOKUP_HMAC_KEY is missing a value
```

Vérifié sur `develop` (`b7a1c6b`, `docker-compose.yml:201`) : cette variable
est bien requise (`${PII_LOOKUP_HMAC_KEY:?…}`) mais **n'apparaît pas** dans
`env.example` malgré un message d'erreur qui renvoie vers `.env.example`
(fichier qui n'existe pas sous ce nom dans ce dépôt — c'est `env.example`,
sans le point). Le fichier `.env` local était daté du jour même (9 septembre,
15h41, soit juste avant/au tout début de cette tâche) — cohérent avec le
constat plus bas qu'un autre processus travaillait activement sur cette
stack pendant la fenêtre de test. Les conteneurs déjà démarrés fonctionnaient
normalement (la variable était visiblement présente dans l'environnement du
shell qui les avait lancés/recréés, juste pas dans le `.env` lu par le mien) :
la vivacité de la stack a donc été vérifiée directement en GraphQL plutôt que
via `docker compose ps`. Ni `.env` ni `env.example` n'ont été modifiés par
cette tâche — signalé ici comme une friction rencontrée et un gap de
documentation potentiel (`env.example` incomplet, message d'erreur pointant
un nom de fichier inexistant), pas corrigé (touche un secret, hors périmètre
d'une tâche d'observation).

## Jeu de données réellement disponible (limite documentée, pas contournée)

Interrogé en direct via GraphQL (`abonnesCount`/`facturesCount`) avant de
lancer les scripts : **32 abonnés**, **36 factures**. C'est un jeu de données
réel mais modeste — plus restreint qu'un scénario de charge idéal (le README
ne fixe aucun minimum), suffisant pour exercer la pagination et les jointures
gRPC réelles (`factures` enrichit chaque ligne avec un `ListAbonnes` +
`ListCampagnes`), mais pas représentatif d'un volume de production. Aucune
tentative de contourner la limite en relançant un script de seed.

Les comptes de démo (`demo_admin`/`Demo1234!`) restent valides malgré la
purge de données métier du 27 août — cohérent avec une purge qui aurait visé
les données métier (abonnés/campagnes/factures) sans supprimer les comptes
utilisateurs eux-mêmes. Constat factuel, pas d'hypothèse creusée plus loin
(hors périmètre de cette tâche).

## Machine et charge de fond — pas un banc de mesure isolé

- **Matériel (vu depuis ce shell) :** 11 CPU logiques, ~18 Gio de RAM
  (`sysctl hw.ncpu hw.memsize`), macOS, Docker Desktop (VM
  `Virtualization.framework`).
- **Charge de fond AVANT tout test de cette tâche :** `uptime` → load average
  7.38 (1 min) sur 11 cœurs — déjà élevée. `docker ps -a` recense en
  permanence sur ce poste, en plus des deux piles SGFE : une pile
  d'observabilité complète (Grafana, Loki, Tempo, Prometheus, Alloy,
  GlitchTip, Pyroscope, Uptime Kuma — 12 conteneurs), un projet `gp-formuloo`
  (plusieurs services + Postgres + Redis + RabbitMQ), un projet `podiq`,
  plusieurs instances Postgres ad hoc (`ft*`, `pg-*`), et un `minikube`
  arrêté. Ce poste sert visiblement plusieurs projets et plusieurs sessions
  en parallèle — pas une machine dédiée au test de charge.
- **Pendant la fenêtre de test**, `ps aux` a montré simultanément : deux
  autres process `claude -c --chrome` actifs (sessions distinctes, l'une
  depuis dimanche 19h, l'autre depuis lundi 13h), un `pytest -q` sur
  `gp-formuloo-backend/services/svc-notifications` puis `svc-issues`
  (projet sans rapport), et surtout **`ng test --no-watch (sgfe-frontend)`**
  puis **`ng build --configuration production (sgfe-frontend)`** — donc une
  autre session travaillant, en parallèle de ce test de charge, sur ce
  dépôt frontend lui-même.

## Interférence directe détectée : redéploiement concurrent de la stack testée

Entre le run n°1 et le run n°2 (voir plus bas), une tentative de re-test léger
a échoué avec `connect: connection refused` sur `:8443`. `docker ps` a montré
juste après tous les conteneurs applicatifs de `sgfe-backend` recréés
(`Up 4 secondes`, `Up 23 secondes`, etc., contre `Up 2 jours` juste avant).
`docker events --since 10m` confirme, horodaté 15:48:18, une opération
`docker compose` avec `com.docker.compose.replace=gateway-1`,
`com.docker.compose.replace=nginx-1`, `com.docker.compose.replace=campagne-service-1`,
etc. — projet `sgfe-backend`, `working_dir=/Users/apple/Documents/SGFE/SGFE-backend`,
donc **exactement la stack visée par ce test**, redéployée par un tiers (une
autre session/agent) pendant la fenêtre de mesure. `sgfe-backend-abonne-postgres-replica-1`
(le conteneur en erreur noté plus haut) a disparu de `docker ps` après ce
redéploiement — vraisemblablement nettoyé par ce même travail concurrent,
sans lien avec cette tâche. Un même schéma a été observé sur le frontend :
`sgfe-frontend-frontend-1`, `Up 2 jours` en tout début de tâche, est passé à
`Up 5 minutes` un peu après, cohérent avec le `ng build --configuration production`
vu dans `ps aux` juste avant.

**Conséquence directe pour l'interprétation des chiffres ci-dessous :** le
run n°2 a eu lieu alors que le load average était monté à 24.3 (1 min) sur
11 cœurs — plus de deux fois le nombre de cœurs disponibles — du fait de ce
redéploiement concurrent additionné aux builds/tests Angular d'une autre
session. Ce n'est donc pas une mesure de la capacité de SGFE en isolation ;
c'est une mesure de SGFE **plus** une contention hôte réelle mais externe.
Documenté ici en détail précisément parce que la consigne demandait de
signaler ce genre de situation plutôt que de la maquiller ou de relancer en
boucle jusqu'à obtenir un chiffre propre.

## Résultats — `loadtest/parcours-metier.js` (backend, ce dépôt)

Commande utilisée (profil par défaut du script, non modifié) :

```bash
BASE_URL=https://localhost:8443 K6_USER=demo_admin K6_PASSWORD='Demo1234!' \
k6 run --insecure-skip-tls-verify loadtest/parcours-metier.js
```

k6 2.1.0 (`brew`, déjà installé sur ce poste — pas eu besoin de l'installer).

### Run n°1 (15:44–15:47, avant que l'interférence ci-dessus soit détectée)

| Métrique | Valeur |
|---|---|
| Requêtes HTTP | 1316, **0 % d'échec** |
| Itérations | 161 complètes, 3 interrompues (fin de descente) |
| `http_req_duration` global | avg 1.07 s · méd 588 ms · p90 2.62 s · **p95 4.07 s** · max 7.22 s |
| Débit effectif | ~8.2 req/s (le README calculait ~14-15 req/s en environnement calme — non atteint ici) |
| `checks` | 2626/2626 réussis (100 %) |

Seuils par requête nommée (seuil documenté → mesuré) :

| Requête | Seuil | p95 mesuré | Résultat |
|---|---|---|---|
| `login` | <800 ms | 444 ms | ✅ |
| `abonnes_page` | <800 ms | 4.07 s | ❌ |
| `abonnesCount` | <500 ms | 4.07 s | ❌ |
| `getAbonne` | <800 ms | 4.68 s | ❌ |
| `abonnes_recherche` | <800 ms | 3.07 s | ❌ |
| `factures_page` | <1200 ms | 3.95 s | ❌ |
| `facturesCount` | <500 ms | 3.92 s | ❌ |
| `dashboard` | <800 ms | 3.72 s | ❌ |
| `statsGlobales` | <800 ms | 3.85 s | ❌ |

`http_req_failed` : 0.00 % (seuil `<1 %` respecté) — **aucune erreur, juste
lent**. C'est la donnée la plus importante de ce run : ni 5xx, ni erreur
GraphQL, ni check en échec — la dégradation observée est uniquement de la
latence, pas de la casse fonctionnelle.

### Run n°2 (15:49–15:52, pendant l'interférence confirmée ci-dessus)

| Métrique | Valeur |
|---|---|
| Requêtes HTTP | 636, **0 % d'échec GraphQL** (mais voir note 499 ci-dessous) |
| Itérations | 73 complètes, **17 interrompues** (itérations de 40 à 75 s, coupées par la fin de descente) |
| `http_req_duration` global | avg 3.72 s · méd 736 ms · p90 6.75 s · **p95 18.92 s** · max **44.47 s** |
| Débit effectif | ~3.9 req/s |
| `checks` | 1266/1266 réussis (100 %) |

Pire cas par requête : `factures_page`/`facturesCount` à **p95 42.4-42.5 s**
(seuil 1200/500 ms) — cohérent avec le commentaire déjà présent dans le
script indiquant que `factures` déclenche un `ListAbonnes` + `ListCampagnes`
complets côté Gateway pour enrichir chaque ligne
(`gateway/schema/facturation_queries.py::_enrichir_factures`) : c'est
mécaniquement la requête la plus exposée à toute contention, avant même de
parler de charge k6.

Logs d'accès nginx sur la fenêtre du run n°2 : **0** occurrence de `429`
(la limite `30 r/s`/`burst 60` documentée dans le README n'a donc jamais été
le facteur limitant, conforme au calcul du README) ; en revanche **24**
requêtes journalisées en **`499`** (client ayant fermé la connexion avant
réponse) — cohérent avec les 17 itérations interrompues : k6 a coupé des VUs
dont la requête en cours dépassait le délai de grâce de fin de palier
(`gracefulRampDown: 10s`), pas une erreur serveur.

### Pourquoi ce n'est pas (nécessairement) un problème d'architecture SGFE

`docker stats` relevé pendant/juste après le run n°2 : **tous** les
conteneurs `sgfe-backend-*` restaient sous 10 % CPU chacun (gateway 0.69 %,
services Django 0.2-7 %, postgres 0-4 %) — aucun conteneur individuel n'était
saturé. Le load average hôte, lui, était à 24 sur 11 cœurs. Cette combinaison
pointe vers une contention au niveau de l'hôte (trop de charge concurrente,
partagée entre beaucoup de projets et sessions, cf. section précédente)
plutôt que vers un verrou ou une inefficacité interne à un service SGFE
précis — mais ce n'est **pas une conclusion définitive** : sans un run
propre sur machine isolée, impossible de trancher complètement la part
« contention hôte » vs. « comportement réel de SGFE à 20 VUs ». C'est
exactement pour ça que le run n°1, plus proche d'un environnement « juste
occupé » (avant que l'interférence directe soit détectée), reste la mesure
la plus utilisable des deux — et elle dépasse déjà largement les seuils
documentés (p95 ~4 s contre 800 ms visés).

## Résultats — `SGFE-frontend/loadtest/basic.js` (profil léger, 5 VUs)

```bash
BASE_URL=https://localhost:8443 K6_USER=demo_admin K6_PASSWORD='Demo1234!' \
k6 run --insecure-skip-tls-verify loadtest/basic.js
```

Exécuté avec le profil par défaut du script (5 VUs, montée/descente 10 s,
palier 20 s — plus léger par construction que `parcours-metier.js`), environ
une minute après le run n°2, alors que le load average retombait un peu
(17.6 → tout en restant élevé).

| Métrique | Valeur |
|---|---|
| Requêtes HTTP | 536, **0 % d'échec** |
| Itérations | 107 complètes, 0 interrompue |
| `http_req_duration` global | avg 91.9 ms · méd 61.3 ms · p90 193 ms · **p95 262 ms** |
| Débit effectif | ~11.9 req/s |
| `checks` | 1072/1072 réussis (100 %) |

| Requête | Seuil | p95 mesuré | Résultat |
|---|---|---|---|
| `abonnes` | <800 ms | 357 ms | ✅ |
| `campagnes` | <800 ms | 197 ms | ✅ |
| `impayes` | <800 ms | 234 ms | ✅ |
| `statsGlobales` | <800 ms | 250 ms | ✅ |
| `configs` | <800 ms | 204 ms | ✅ |
| `login` | <800 ms | **3.1 s** | ❌ (n=1, voir note) |

Seul `login` dépasse son seuil — mais `login` n'est appelé **qu'une seule
fois** dans `setup()`, pas à chaque itération : p95 sur un échantillon de 1
n'a pas de sens statistique, c'est juste que cet unique appel est tombé au
moment où le load average hôte culminait. Les cinq lectures réellement
répétées sous charge (`abonnes`, `campagnes`, `impayes`, `statsGlobales`,
`configs`) passent toutes largement sous 800 ms, y compris avec un load
average hôte encore élevé au moment du run.

**Ce contraste est le résultat le plus utile des trois runs.** À faible
concurrence (5 VUs), y compris sur cette même machine bruyante, la Gateway
répond vite (p95 262 ms). C'est seulement au palier à 20 VUs, en particulier
lors du redéploiement concurrent, que les latences explosent. Impossible de
séparer proprement, avec ces trois runs, la part due à « 20 VUs contre 5 »
de la part due à « redéploiement + builds concurrents pendant le run à 20
VUs » — les deux facteurs coïncident dans le temps. Un vrai test de montée
en charge propre (10, puis 20 VUs, sur machine isolée) serait nécessaire pour
trancher.

## Ce que ces scripts ne mesurent toujours pas

Le scénario métier cité dans la demande initiale — plusieurs agents terrain
saisissant des relevés en même temps pendant qu'un encaissement FIFO cascade
sur plusieurs factures et qu'un autre agent corrige un relevé — **n'a
aucune couverture k6 existante, ici ou côté frontend**. Les deux scripts
(`parcours-metier.js`, `basic.js`) sont délibérément en lecture seule :
aucune mutation (`saisirIndex`, `enregistrerPaiement`, `corrigerReleve`,
etc.) n'y est exercée, pour la raison documentée dans leurs propres
en-têtes — aucune mutation de nettoyage sûre n'existe côté `abonne`, et
muter les vrais abonnés/campagnes de la base partagée sans pouvoir revenir
en arrière n'est pas acceptable pour un script rejouable. Écrire un script
de charge dédié à ce scénario précis demanderait des données jetables
(campagne + abonnés créés puis détruits proprement dans un `teardown()`) —
un chantier à part entière, hors périmètre de cette tâche d'observation.
**Ce point reste donc entièrement ouvert** : ni ce document ni les scripts
existants ne disent quoi que ce soit sur le comportement de SGFE face à un
encaissement concurrent à une correction de relevé.

## Limites de cet exercice — résumé

1. **Machine partagée, non isolée** : au moins deux autres sessions actives
   pendant la fenêtre de test, dont une qui a redéployé la stack testée
   elle-même. Les chiffres de latence ci-dessus ne sont donc pas une mesure
   propre de la capacité de SGFE — voir « Facteur de confusion ».
2. **Jeu de données restreint** : 32 abonnés / 36 factures, pas relancé de
   seed pour l'étoffer (règle du projet respectée).
3. **Aucune mutation testée** : le scénario d'écriture concurrente (FIFO
   paiement + correction de relevé) décrit dans la demande initiale n'est
   couvert par aucun des deux scripts existants.
4. **`docker compose ps` non fonctionnel depuis ce shell** (variable d'env
   manquante côté `.env` local, non lié à cette tâche) — vivacité vérifiée
   directement en GraphQL à la place.
5. **`curl` indisponible dans ce contexte d'exécution** (bloqué par la
   politique de permissions de la session) — les vérifications de
   connectivité ont été faites via de petits scripts k6 ad hoc plutôt que
   `curl`, fonctionnellement équivalent pour ce besoin.

## Aucun correctif appliqué

Conformément à la consigne, aucun correctif n'a été tenté : les latences
élevées du run n°2 ne sont pas un bug à une ligne évident (elles sont très
probablement dominées par la contention hôte externe documentée ci-dessus,
pas par un défaut de code identifiable), et le vrai signal actionnable —
`factures_page`/`facturesCount` systématiquement les plus lentes des neuf
requêtes, cohérent avec leur coût gRPC intrinsèque déjà documenté dans le
script — mérite un vrai profilage (traces OTel par exemple, si la chaîne
d'export vers `otel-collector` fonctionnait — voir note ci-dessous) avant
toute tentative de correctif, pas une modification à l'aveugle en réaction à
un seul run bruité.

**Note incidente, hors périmètre, non creusée :** les logs de `gateway`
montrent, en continu et indépendamment de ce test, des échecs de résolution
DNS vers `otel-collector` (`Failed to resolve 'otel-collector'`) — la chaîne
d'observabilité décrite dans `CLAUDE.md` comme fonctionnelle côté traces ne
semble donc pas active sur cette instance au moment de ce run. Constat
factuel en passant, pas vérifié plus avant (hors sujet de cette tâche).

## Arrêt (ou non) des stacks

**Aucune des deux stacks n'a été arrêtée.** Preuve directe rassemblée
pendant cette tâche (redéploiement concurrent de `sgfe-backend`, `ng build`/
`ng test` concurrents sur `sgfe-frontend`, cf. sections précédentes) qu'un
autre travail en cours dans cet environnement dépend des deux piles restées
démarrées. Les deux stacks ont été laissées exactement dans l'état observé
en fin de tâche : `sgfe-backend` et `sgfe-frontend` toutes deux `healthy`
(à l'exception préexistante de `whatsapp-service`, sans lien avec ce test).
