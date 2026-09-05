# Test de charge k6 — parcours métier (backend)

`parcours-metier.js` exerce la Gateway GraphQL de **ce dépôt**
(`https://localhost:8443/graphql` en local, derrière nginx) avec des parcours
métier réalistes, pas des lectures isolées :

1. **Authentification** — `login`, un seul token obtenu en `setup()` et
   réutilisé par tous les VUs pour tout le run (comme un vrai client qui
   garde sa session).
2. **Liste paginée des abonnés** — `abonnes(limit, offset)` sur une page
   choisie au hasard parmi celles réellement disponibles, + `abonnesCount`.
3. **Consultation d'un abonné + recherche filtrée** — `abonne(id)` puis
   `abonnes(statut: ACTIF, …)`. Remplace une mutation réelle (voir
   « Pourquoi pas d'écriture » ci-dessous).
4. **Liste paginée des factures** — `factures(limit, offset)` + `facturesCount`.
5. **Dashboard reporting** — `dashboard` + `statsGlobales`.

Profil de charge en paliers (`options.scenarios`, executor `ramping-vus`) :
montée progressive → plateau → descente, pas un pic brutal.

## Ce script vs. `SGFE-frontend/loadtest/basic.js`

Ce dépôt (backend) n'avait **aucun** script k6 avant cette tâche. Le script
documenté par `AUDIT_SGFE.md` (§8·K, PR #143, « 2-3 requêtes GraphQL ») vit en
réalité dans **le dépôt frontend** (`SGFE-frontend/loadtest/basic.js`,
lui-même déjà étendu depuis à 6 lectures + profil en paliers) — les deux
dépôts ont des numérotations de PR indépendantes, d'où la confusion possible
en le cherchant uniquement ici. Ce fichier-ci n'a donc rien « remplacé » au
sens strict : c'est un script neuf, propre au backend, qui vise directement
la Gateway sans passer par le frontend Angular. Les deux scripts se
recoupent forcément (même Gateway visée) ; le frontend n'a pas été modifié
par cette tâche, cantonnée à ce dépôt.

## Pourquoi pas de mutation réelle (`createAbonne`, etc.)

Vérifié dans `gateway/schema/abonne_mutations.py` : il n'existe **aucune**
mutation de suppression dure d'un abonné. `createAbonne` en boucle créerait
donc des abonnés impossibles à purger proprement ensuite — exactement le
piège à éviter. Les seules mutations qui touchent le statut d'un abonné sont
`suspendreAbonne`/`reactiverAbonne` (réversibles, mais modifient un **vrai**
abonné de la base partagée) et `resilierAbonne`/`anonymiserAbonne`
(délibérément irréversibles, RGPD) — aucune n'est sûre à rejouer en boucle
contre une base de dev partagée. Le parcours « `GetAbonne` + recherche »
(deux lectures, trivialement idempotent) en tient lieu.

## Pourquoi pas `statsParMois` dans le dashboard

`gateway/schema/stats_queries.py::stats_par_mois` fait un fan-out gRPC de 2
appels **par campagne existante** — un coût qui dépend du contenu de la base
ciblée, pas du profil de charge qu'on veut mesurer. `dashboard` +
`statsGlobales` sont représentatifs d'un chargement de tableau de bord à
coût prévisible (un seul appel gRPC chacun).

## Pourquoi le filtre `statut` fait office de « recherche »

Aucune query `rechercherAbonnes` ni argument de recherche texte n'existe
côté Gateway (vérifié dans tout `gateway/schema/`) : le filtrage par
nom/quartier est fait côté client après un chargement complet. Le seul
filtre server-side réel sur `abonnes` est `statut` — c'est lui qui est
exercé ici.

## La contrainte qui dimensionne le profil de charge : nginx, pas une intuition

`nginx/default.conf` définit `limit_req_zone … rate=30r/s` avec `burst=60
nodelay` sur `/graphql` (429 au-delà). Un run k6 local tape depuis une seule
IP source : tous les VUs partagent ce même budget de 30 req/s. Le profil
ci-dessous (VUs, temps de réflexion) est calculé pour rester nettement
en dessous — sinon le test échouerait sur du 429 nginx, pas sur une vraie
limite applicative :

- 8 requêtes GraphQL par itération, ~4 pauses de réflexion (1-2,5 s) + 1
  pause de fin de session (2-4 s) ≈ 10-12 s d'itération (mesuré à 11,4 s sur
  un mock local, voir plus bas).
- À `VUS_CIBLE=20` : 20 × 8 / ~11 s ≈ 14-15 req/s en régime établi — environ
  la moitié du budget nginx, marge volontaire pour la variance et le
  démarrage des VUs pendant la montée en charge.

## Seuils (`thresholds`) — ce qui est mesuré vs. ce qui est du bon sens

**Aucune baseline mesurée n'existe sur cet environnement** (pas de staging,
cf. `AUDIT_SGFE.md` §8·K). En conséquence :

- `http_req_failed: rate<0.01` — plancher de bon sens, pas un chiffre mesuré.
- `http_req_duration: p(95)<800ms` — reprend la valeur déjà en usage côté
  frontend pour la même Gateway en local, pour ne pas inventer un second
  chiffre sans plus de justification que le premier.
- Seuils par requête nommée : `abonnesCount`/`facturesCount` à 500 ms
  (hypothèse non mesurée : un scalaire coûte moins qu'une liste sérialisée),
  `factures_page` à 1200 ms (`factures` déclenche en plus, côté Gateway, un
  `ListAbonnes` + un `ListCampagnes` complets pour enrichir chaque ligne —
  voir `facturation_queries.py::_enrichir_factures`), le reste à 800 ms.

**À recalibrer avec de vraies mesures dès qu'un environnement de staging
existera** — ce n'est pas fait ici faute d'environnement, pas par oubli.

## Données de test

Ce script **ne crée pas** ses propres données dans `setup()` : il suppose
une stack `docker compose up` locale déjà peuplée (données de dev réelles,
ou `scripts/seed_demo.sh` déjà joué). Choix le plus simple, et le seul
cohérent avec l'absence de mutation de nettoyage côté abonné (voir plus
haut). Si la base ciblée est vide, `setup()` échoue avec un message
explicite plutôt que de laisser tourner un scénario sur des listes vides
sans le dire.

⚠️ **Ne pas relancer `scripts/seed_demo.sh` pour les besoins de ce script**
sans vérifier d'abord l'état réel de la base ciblée.

## Compte utilisé

`abonnes`/`abonne` : ADMIN uniquement. `factures`/`dashboard`/
`statsGlobales` : ADMIN + COMPTABLE. Un compte ADMIN couvre les cinq
requêtes (voir `CLAUDE.md` § Rôles et permissions). Comptes de démo
(`scripts/seed/README.md`, mot de passe commun `Demo1234!`) :
`demo_admin`, `demo_comptable`, `demo_superviseur`, `demo_agent`.

## Prérequis

- [k6](https://k6.io/) installé (`brew install k6`).
- Stack backend en local via `docker compose up -d --build`, servie en HTTPS
  sur `https://localhost:8443` — certificat auto-signé de dev à générer une
  fois avant le premier démarrage : `./scripts/generate-nginx-cert.sh` (sans
  lui, nginx refuse de démarrer, fail-fast volontaire).
- Un compte ADMIN valide, ex. `demo_admin`/`Demo1234!`.

**Avant de lancer quoi que ce soit contre une stack locale**, vérifier
`docker compose ps` : si un autre agent ou une autre session s'en sert déjà,
ne pas la solliciter en plus sans coordination — même un test « en lecture
quasi-seule » ajoute de la charge sur un environnement partagé.

## Lancer

```bash
BASE_URL=https://localhost:8443 \
K6_USER=demo_admin K6_PASSWORD=Demo1234! \
k6 run --insecure-skip-tls-verify loadtest/parcours-metier.js
```

`--insecure-skip-tls-verify` : nécessaire tant que nginx sert le certificat
auto-signé de dev — jamais à utiliser contre un environnement réel.

Réglages optionnels :

- `K6_VUS_CIBLE` — VUs au palier stable (défaut `20`)
- `K6_MONTEE` — durée de montée, 0 → `K6_VUS_CIBLE` (défaut `30s`)
- `K6_PALIER` — durée du palier stable (défaut `90s`)
- `K6_DESCENTE` — durée de la descente (défaut `30s`)
- `K6_PAGE_SIZE` — taille de page pour la pagination (défaut `10`)

Exemple pour un essai minimal (2 VUs, montée/descente 5s, palier 10s) :

```bash
BASE_URL=https://localhost:8443 K6_USER=demo_admin K6_PASSWORD=Demo1234! \
K6_VUS_CIBLE=2 K6_MONTEE=5s K6_PALIER=10s K6_DESCENTE=5s \
k6 run --insecure-skip-tls-verify loadtest/parcours-metier.js
```

## Ce qui a été vérifié depuis cet environnement, et ce qui ne l'a PAS été

**Vérifié** :

- Toutes les queries/mutations citées existent dans le schéma actuel, avec
  les rôles indiqués (lu directement dans `gateway/schema/*.py`).
- Le script compile et ses `options`/`thresholds` sont valides — `k6 inspect
  loadtest/parcours-metier.js` (cette version de k6, 2.1.0, n'a pas de
  `--dry-run` ; `inspect` est l'équivalent disponible : il parse le script et
  expose `options` sans lancer de VU ni toucher le réseau).
- **La logique JS elle-même (`setup()` + `default()`, y compris la
  pagination aléatoire, le `http.batch()`, le passage de données entre
  `setup()` et les itérations, et les 18 `check()`) a été exécutée pour de
  vrai** contre un mock GraphQL local jetable (pas la vraie stack) : run k6
  complet, 18/18 checks au vert, 12 requêtes HTTP, itération mesurée à
  11,4 s — cohérent avec le calcul de débit ci-dessus. Ce mock ne valide en
  rien les temps de réponse réels, la charge gRPC réelle, ni le contenu
  réel des données — seulement que le script n'a pas de bug d'exécution.

**PAS vérifié** (nécessite une stack vivante, volontairement non démarrée
ici pour ne pas perturber une stack docker compose partagée avec d'autres
agents) :

- Que les seuils de latence (`p(95)<800ms`, etc.) tiennent en pratique contre
  la vraie Gateway/les vrais services gRPC.
- Que le calcul de débit (~14-15 req/s à 20 VUs) est exact une fois les
  vraies latences réseau/gRPC mesurées (le mock répond en microsecondes, pas
  représentatif).
- Que la base ciblée contient effectivement des abonnés/factures au moment
  du run (dépend de l'état réel de la stack visée).

**À faire manuellement avant de considérer ce script comme validé** :
lancer `docker compose up -d --build` sur une stack backend locale (pas la
stack partagée en cours d'utilisation), vérifier qu'elle est peuplée
(`scripts/seed_demo.sh` si besoin, après vérification explicite), puis
`k6 run --insecure-skip-tls-verify loadtest/parcours-metier.js` en conditions
réelles.

## Intégration CI

**Pas de job CI** pour ce script, choix délibéré : `SGFE-frontend/loadtest/basic.js`
n'est câblé dans aucun workflow CI de son propre dépôt (vérifié :
`grep -i k6 .github/workflows/*.yml` n'y remonte rien) ; ce dépôt n'a pas non
plus de job k6 avant cette tâche. Ajouter un job CI ici demanderait de faire
tourner toute la stack Docker Compose (21 services) dans le runner GitHub
Actions — un chantier à part entière (temps de démarrage, healthchecks,
secrets de test), hors périmètre de cette tâche, et risqué à rendre
bloquant sans seuils éprouvés (voir ci-dessus : aucune baseline mesurée).
Rester cohérent avec le seul précédent existant (script manuel, hors CI)
plutôt que d'ajouter un nouveau job non réfléchi.
