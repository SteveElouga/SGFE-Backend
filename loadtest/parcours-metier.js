import http from 'k6/http';
import { check, sleep } from 'k6';

/**
 * Test de charge k6 — parcours métier réalistes contre la Gateway GraphQL
 * de CE dépôt (SGFE-backend), exposée en local via nginx sur
 * `https://localhost:8443/graphql`.
 *
 * ── Pourquoi ce script existe, et pourquoi il n'« écrase » rien ici ──────────
 * AUDIT_SGFE.md (§8·K) documente un script k6 existant limité à 2-3 requêtes
 * GraphQL. Après recherche, ce script (`loadtest/basic.js`) vit en réalité
 * dans le dépôt FRONTEND (`SGFE-frontend/loadtest/basic.js`, ajouté par la PR
 * #143 *de ce dépôt-là*, pas du backend — les deux dépôts ont des numérotations
 * de PR indépendantes, d'où la confusion possible) : il n'y a jamais eu de
 * script k6 dans SGFE-backend, donc rien à remplacer ici au sens strict. Ce
 * fichier est un script k6 nouveau, propre au backend, qui teste directement
 * la Gateway (sans passer par le frontend Angular), ce qui a du sens pour un
 * test de charge côté backend : on isole la couche API du rendu client.
 * Le script frontend n'a pas été modifié (hors périmètre de cette tâche, qui
 * est cantonnée à ce dépôt) ; il a seulement été lu pour éviter de dupliquer
 * ses choix sans les comprendre. Les deux scripts se recoupent forcément
 * puisqu'ils visent la même Gateway — ce n'est pas un défaut à corriger dans
 * cette tâche, juste un fait à savoir.
 *
 * ── Ce que ce script couvre (parcours métier, pas des lectures isolées) ─────
 *   1. Authentification — `login`, token réutilisé par tous les VUs (une
 *      seule connexion pour tout le run, comme un vrai client qui garde sa
 *      session, pas une reconnexion à chaque itération).
 *   2. Lecture paginée des abonnés — `abonnes(limit, offset)` sur une page
 *      choisie au hasard parmi les pages réellement disponibles (pas
 *      systématiquement la première), + `abonnesCount` (un tableau paginé
 *      réel demande les deux pour afficher « page X / Y »).
 *   3. Consultation d'un abonné + recherche filtrée — `abonne(id)` puis
 *      `abonnes(statut: ACTIF, ...)`. C'est le parcours « non destructif et
 *      idempotent » demandé à la place d'une mutation réelle — voir plus bas
 *      pourquoi aucune mutation n'est exercée.
 *   4. Lecture paginée des factures — `factures(limit, offset)` + `facturesCount`,
 *      même logique de pagination réaliste que les abonnés.
 *   5. Dashboard reporting — `dashboard` + `statsGlobales`, les deux queries
 *      bon marché (un seul appel gRPC chacune, pas de fan-out) qu'un vrai
 *      écran de tableau de bord charge ensemble à l'arrivée sur la page.
 *
 * ── Pourquoi PAS de mutation (`createAbonne` ou autre) ──────────────────────
 * Vérifié dans `gateway/schema/abonne_mutations.py` : il n'existe AUCUNE
 * mutation de suppression dure d'un abonné. Les seules mutations qui changent
 * un statut sont `suspendreAbonne`/`reactiverAbonne` (réversibles mais
 * modifient un VRAI abonné de la base partagée — exactement ce qu'interdit la
 * consigne « ne jamais muter les données de démo sans le dire ») et
 * `resilierAbonne`/`anonymiserAbonne` (délibérément irréversibles, RGPD).
 * Autrement dit : `createAbonne` en boucle créerait des abonnés qu'aucune
 * mutation ne permet ensuite de purger proprement — exactement le piège que
 * la consigne de cette tâche demande d'éviter. Le parcours « GetAbonne +
 * recherche » (deux lectures) est donc la version réaliste ET sûre de ce
 * parcours, comme suggéré en cas d'absence d'écriture sûre.
 *
 * ── Pourquoi PAS de `statsParMois` dans le dashboard ────────────────────────
 * Vérifié dans `gateway/schema/stats_queries.py` : `statsParMois` fait un
 * fan-out gRPC de 2 appels PAR campagne existante (coût qui grandit avec les
 * données, indépendamment du nombre de VUs). L'inclure rendrait le coût du
 * scénario dépendant du contenu de la base ciblée plutôt que du profil de
 * charge qu'on veut mesurer. `dashboard` + `statsGlobales` restent
 * représentatifs d'un chargement de tableau de bord à coût prévisible.
 *
 * ── Pourquoi PAS de recherche texte serveur ──────────────────────────────────
 * Vérifié dans tout `gateway/schema/` : aucune query `rechercherAbonnes` ni
 * argument de recherche textuelle n'existe. Le filtrage par nom/quartier est
 * fait côté client (frontend) après un chargement complet — donc côté
 * Gateway, la seule « recherche » server-side réellement disponible est le
 * filtre `statut` sur `abonnes`. C'est ce que ce script appelle recherche.
 *
 * ── La contrainte qui dimensionne tout : nginx, pas une intuition ──────────
 * `nginx/default.conf` définit `limit_req_zone $binary_remote_addr
 * zone=gw_limit rate=30r/s` avec `burst=60 nodelay` sur `/graphql` (429
 * au-delà). Un run k6 lancé localement tape depuis une seule IP source : TOUS
 * les VUs partagent donc ce même budget de 30 req/s. C'est une contrainte
 * réelle et vérifiable de cet environnement, pas un chiffre inventé — le
 * profil de charge ci-dessous (nombre de VUs, temps de réflexion entre
 * requêtes) est calculé pour rester confortablement en dessous, sans quoi le
 * test échouerait sur du 429 nginx et non sur une vraie limite applicative.
 * Calcul (voir DEFAULT_PAGE_SIZE et les sleep() dans le scénario) : 8 requêtes
 * GraphQL par itération, ~4 pauses de réflexion (1-2,5 s) + 1 pause de fin de
 * session (2-4 s) ≈ 10-12 s d'itération. À VUS_CIBLE=20 : 20 × 8 / ~11 s ≈
 * 14-15 req/s en régime établi — environ la moitié du budget nginx, marge
 * volontaire pour absorber la variance et le démarrage des VUs pendant la
 * montée en charge.
 *
 * ── Données de test — choix : réutiliser une stack déjà peuplée ─────────────
 * Ce script NE crée PAS ses propres données dans `setup()` : il suppose que
 * la stack ciblée (`docker compose up` local) a déjà des abonnés/factures
 * (données réelles de dev, ou `scripts/seed_demo.sh` déjà joué). C'est le
 * choix le plus simple, et le seul cohérent avec l'absence de mutation de
 * nettoyage possible côté abonné (voir plus haut). Si la base est vide,
 * `setup()` échoue avec un message explicite plutôt que de laisser le
 * scénario tourner sur des listes vides sans le dire.
 * Ne PAS relancer `scripts/seed_demo.sh` pour les besoins de ce script sans
 * vérifier d'abord l'état réel de la base ciblée.
 *
 * ── Compte utilisé ───────────────────────────────────────────────────────────
 * `abonnes`/`abonne` sont réservées ADMIN ; `factures`/`dashboard`/
 * `statsGlobales` sont ADMIN + COMPTABLE. Un compte ADMIN couvre donc les
 * cinq requêtes de ce script (voir CLAUDE.md § Rôles et permissions). En
 * local avec les comptes de démo (`scripts/seed/README.md`) :
 * `K6_USER=demo_admin K6_PASSWORD=Demo1234!`.
 *
 * ── Usage ────────────────────────────────────────────────────────────────────
 *   BASE_URL=https://localhost:8443 \
 *   K6_USER=demo_admin K6_PASSWORD=Demo1234! \
 *   k6 run --insecure-skip-tls-verify loadtest/parcours-metier.js
 *
 * `--insecure-skip-tls-verify` : certificat auto-signé de dev
 * (`./scripts/generate-nginx-cert.sh`, prérequis avant le premier
 * `docker compose up` — voir CLAUDE.md). Jamais à utiliser contre un
 * environnement réel.
 *
 * Réglages optionnels :
 *   K6_VUS_CIBLE  — VUs au palier stable (défaut 20)
 *   K6_MONTEE     — durée de montée, 0 → K6_VUS_CIBLE (défaut '30s')
 *   K6_PALIER     — durée du palier stable (défaut '90s')
 *   K6_DESCENTE   — durée de la descente (défaut '30s')
 *   K6_PAGE_SIZE  — taille de page pour la pagination (défaut 10)
 *
 * ── Ce qui a été vérifié depuis cet environnement, et ce qui ne l'a PAS été ─
 * Vérifié : toutes les queries/mutations citées existent bien dans le schéma
 * actuel (lues dans `gateway/schema/*.py`), avec les rôles indiqués ; le
 * script compile et ses `options` sont valides (`k6 inspect
 * loadtest/parcours-metier.js` — cette version de k6, 2.1.0, n'a pas de
 * `--dry-run`, `inspect` est l'équivalent disponible : il parse le script et
 * expose `options` sans lancer de VU ni toucher le réseau, donc SANS valeur
 * pour les checks/thresholds en conditions réelles).
 * PAS vérifié (nécessite une stack vivante, volontairement non démarrée ici
 * pour ne pas perturber une stack partagée) : que les valeurs réellement
 * retournées passent les `check()`, que les seuils de latence tiennent en
 * pratique, que la base ciblée contient au moins un abonné/une facture, et
 * que le calcul de débit ci-dessus (14-15 req/s) est exact une fois les
 * vraies latences réseau/gRPC mesurées. À faire manuellement contre une
 * stack locale avant de considérer ce script comme validé.
 */

const BASE_URL = __ENV.BASE_URL || 'https://localhost:8443';
const IDENTIFIER = __ENV.K6_USER;
const PASSWORD = __ENV.K6_PASSWORD;

const VUS_CIBLE = Number(__ENV.K6_VUS_CIBLE) || 20;
const TAILLE_PAGE = Number(__ENV.K6_PAGE_SIZE) || 10;

export const options = {
  scenarios: {
    parcours_metier: {
      executor: 'ramping-vus',
      startVUs: 0,
      stages: [
        { duration: __ENV.K6_MONTEE || '30s', target: VUS_CIBLE },
        { duration: __ENV.K6_PALIER || '90s', target: VUS_CIBLE },
        { duration: __ENV.K6_DESCENTE || '30s', target: 0 },
      ],
      // Laisse une itération déjà commencée se terminer plutôt que la couper
      // net pendant la descente (sinon les derniers checks échoueraient sur
      // une requête interrompue, pas sur un vrai problème de charge).
      gracefulRampDown: '10s',
    },
  },
  thresholds: {
    // Seuils globaux — filet de sécurité.
    //
    // AUCUNE mesure de baseline n'existe sur cet environnement à ce jour
    // (pas d'environnement de staging, cf. AUDIT_SGFE.md §8·K) : les valeurs
    // ci-dessous ne sont PAS tirées d'un profilage réel de cette Gateway.
    // `http_req_failed: rate<0.01` est un plancher de bon sens (moins de 1 %
    // d'échecs, quel que soit le débit) plutôt qu'un chiffre mesuré.
    // `p(95)<800ms` reprend la valeur déjà en usage côté frontend
    // (`SGFE-frontend/loadtest/basic.js`) pour la même Gateway en local — pas
    // parce qu'elle serait démontrée juste, mais pour ne pas inventer un
    // second chiffre sans plus de justification que le premier. À recalibrer
    // avec de vraies mesures dès qu'un environnement de staging existera.
    http_req_failed: ['rate<0.01'],
    http_req_duration: ['p(95)<800'],
    // Seuils par requête nommée (tag `name`) : une requête lente ne doit pas
    // se diluer dans la moyenne des huit et passer inaperçue.
    'http_req_duration{name:login}': ['p(95)<800'],
    // `abonnesCount`/`facturesCount` : hypothèse (non mesurée) qu'un compte
    // scalaire est moins coûteux qu'une liste sérialisée — seuil plus
    // serré, à corriger si la mesure dit le contraire.
    'http_req_duration{name:abonnesCount}': ['p(95)<500'],
    'http_req_duration{name:facturesCount}': ['p(95)<500'],
    'http_req_duration{name:abonnes_page}': ['p(95)<800'],
    'http_req_duration{name:getAbonne}': ['p(95)<800'],
    'http_req_duration{name:abonnes_recherche}': ['p(95)<800'],
    // `factures` déclenche en plus, côté Gateway, un `ListAbonnes` et un
    // `ListCampagnes` complets pour enrichir chaque ligne (voir
    // `facturation_queries.py::_enrichir_factures`) — plus coûteux qu'une
    // simple liste paginée, seuil volontairement plus large.
    'http_req_duration{name:factures_page}': ['p(95)<1200'],
    'http_req_duration{name:dashboard}': ['p(95)<800'],
    'http_req_duration{name:statsGlobales}': ['p(95)<800'],
  },
};

// ─── Requêtes GraphQL ────────────────────────────────────────────────────────

const LOGIN_QUERY = `
  mutation Login($identifier: String!, $password: String!) {
    login(identifier: $identifier, password: $password) {
      accessToken
      expiresIn
    }
  }
`;

// Même sélection que le frontend (src/app/graphql/queries/abonnes.queries.ts)
// pour une page donnée — `limit`/`offset` réels, vérifiés dans
// `gateway/schema/abonne_queries.py`.
const GET_ABONNES_PAGE = `
  query GetAbonnesPage($limit: Int, $offset: Int) {
    abonnes(limit: $limit, offset: $offset) {
      id
      numeroAbonne
      nom
      prenom
      statut
    }
  }
`;

const GET_ABONNES_COUNT = `
  query GetAbonnesCount {
    abonnesCount
  }
`;

// Détail d'un abonné — ADMIN uniquement, comme `abonnes`.
const GET_ABONNE = `
  query GetAbonne($id: ID!) {
    abonne(id: $id) {
      id
      numeroAbonne
      nom
      prenom
      telephoneWhatsapp
      statut
      compteur {
        numeroCompteur
        quartier
        statut
      }
    }
  }
`;

// « Recherche » réaliste au sens de ce schéma : le seul filtre server-side
// disponible sur `abonnes` est `statut` (pas de recherche texte, voir le
// commentaire d'en-tête). Une page filtrée sur ACTIF, comme un utilisateur
// qui vient de restreindre sa liste.
const GET_ABONNES_RECHERCHE = `
  query GetAbonnesRecherche($limit: Int, $offset: Int) {
    abonnes(statut: ACTIF, limit: $limit, offset: $offset) {
      id
      numeroAbonne
      nom
      prenom
      statut
    }
  }
`;

const GET_FACTURES_PAGE = `
  query GetFacturesPage($limit: Int, $offset: Int) {
    factures(limit: $limit, offset: $offset) {
      factureId
      numeroFacture
      abonneId
      statut
      montant
      dateGeneration
      dateLimitePaiement
      abonneNom
      campagneNom
    }
  }
`;

const GET_FACTURES_COUNT = `
  query GetFacturesCount {
    facturesCount
  }
`;

const GET_DASHBOARD = `
  query GetDashboard {
    dashboard {
      campagneEnCours {
        campagneId
        nomCampagne
        totalAbonnes
        nbReleves
        pourcentageProgression
      }
      facturationEnCours {
        totalFactures
        montantTotalFacture
        nbFacturesPayees
        nbImpayes
      }
      paiementsEnCours {
        montantEncaisse
        montantImpaye
        tauxRecouvrement
      }
    }
  }
`;

// Même sélection que le frontend (queries/stats.queries.ts) : un seul appel
// gRPC côté Gateway, pas de fan-out (voir `reporting_queries.py`).
const GET_STATS_GLOBALES = `
  query GetStatsGlobales {
    statsGlobales {
      consommationTotaleGlobale
      montantTotalFactureGlobal
      montantTotalEncaisseGlobal
      historiqueCampagnes {
        campagneId
        nomCampagne
        totalAbonnes
        nbReleves
        pourcentageProgression
        consommationTotale
      }
    }
  }
`;

// ─── Aides HTTP / GraphQL ────────────────────────────────────────────────────

function headersAvecToken(token) {
  const headers = { 'Content-Type': 'application/json' };
  if (token) headers['Authorization'] = `Bearer ${token}`;
  return headers;
}

/** Requête GraphQL unique (hors batch) — utilisée par `setup()` et pour les
 * étapes du parcours qui doivent s'exécuter l'une après l'autre (pas en
 * parallèle), comme un vrai clic-puis-attente côté utilisateur. */
function graphql(token, query, variables, name) {
  return http.post(`${BASE_URL}/graphql`, JSON.stringify({ query, variables }), {
    headers: headersAvecToken(token),
    tags: { name },
  });
}

/** Élément de `http.batch()` — pour les paires de requêtes qu'un vrai écran
 * déclenche ensemble au chargement (une liste + son compte total, un
 * dashboard + ses stats globales). */
function requeteBatch(token, query, variables, name) {
  return {
    method: 'POST',
    url: `${BASE_URL}/graphql`,
    body: JSON.stringify({ query, variables }),
    params: { headers: headersAvecToken(token), tags: { name } },
  };
}

function sansErreurGraphql(res) {
  try {
    const body = JSON.parse(res.body);
    return !(Array.isArray(body.errors) && body.errors.length > 0);
  } catch {
    return false;
  }
}

function verifier(res, label) {
  check(res, {
    [`${label} → 200`]: (r) => r.status === 200,
    [`${label} → sans erreur GraphQL`]: (r) => sansErreurGraphql(r),
  });
}

/** Page aléatoire parmi celles réellement disponibles — pas systématiquement
 * la première, condition explicite de cette tâche. Avec `total` inconnu ou
 * nul, retombe sur la page 0 (liste vide : rien d'autre à paginer). */
function offsetAleatoire(total, taillePage) {
  const nbPages = Math.max(1, Math.ceil(total / taillePage));
  const page = Math.floor(Math.random() * nbPages);
  return page * taillePage;
}

/** Temps de réflexion entre deux actions d'un même utilisateur — fourchette
 * volontairement modeste : voir le calcul de débit en tête de fichier, c'est
 * ce qui maintient l'ensemble sous le budget nginx (30 req/s par IP source). */
function tempsDeReflexion(min, max) {
  sleep(min + Math.random() * (max - min));
}

// ─── setup() : une seule connexion pour tout le run ─────────────────────────

export function setup() {
  if (!IDENTIFIER || !PASSWORD) {
    throw new Error(
      'K6_USER / K6_PASSWORD requis (compte ADMIN — ex. demo_admin/Demo1234!, voir loadtest/README.md).',
    );
  }

  const loginRes = graphql(null, LOGIN_QUERY, { identifier: IDENTIFIER, password: PASSWORD }, 'login');
  const loginOk = check(loginRes, {
    'login → 200': (r) => r.status === 200,
    'login → sans erreur GraphQL': (r) => sansErreurGraphql(r),
  });
  if (!loginOk) {
    throw new Error(`Échec du login (${loginRes.status}) : ${loginRes.body}`);
  }
  const token = JSON.parse(loginRes.body).data.login.accessToken;

  // Un abonné réel pour le parcours GetAbonne — sans lui, ce parcours ne peut
  // pas tourner (pas d'ID à interroger). Échec explicite plutôt qu'un
  // scénario qui tournerait silencieusement sur des erreurs GraphQL.
  const unAbonneRes = graphql(token, GET_ABONNES_PAGE, { limit: 1, offset: 0 }, 'abonnes_page');
  if (unAbonneRes.status !== 200 || !sansErreurGraphql(unAbonneRes)) {
    throw new Error(`Impossible de lister les abonnés en setup (${unAbonneRes.status}) : ${unAbonneRes.body}`);
  }
  const abonnes = JSON.parse(unAbonneRes.body).data.abonnes;
  if (!abonnes || abonnes.length === 0) {
    throw new Error(
      'Aucun abonné dans la base ciblée : ce script suppose une stack déjà peuplée ' +
        '(données de dev réelles, ou scripts/seed_demo.sh déjà joué — ne pas le relancer sans vérifier, ' +
        "voir loadtest/README.md). Sans abonné, le parcours GetAbonne n'a rien à interroger.",
    );
  }
  const abonneId = abonnes[0].id;

  // Totaux pour calculer des offsets de pagination réalistes (pas juste la
  // page 0) — un total à 0 est traité normalement (offsetAleatoire retombe
  // sur 0), une liste vide n'est pas une erreur pour `factures`.
  const countAbonnesRes = graphql(token, GET_ABONNES_COUNT, undefined, 'abonnesCount');
  const totalAbonnes = sansErreurGraphql(countAbonnesRes) ? JSON.parse(countAbonnesRes.body).data.abonnesCount : 0;

  const countFacturesRes = graphql(token, GET_FACTURES_COUNT, undefined, 'facturesCount');
  const totalFactures = sansErreurGraphql(countFacturesRes) ? JSON.parse(countFacturesRes.body).data.facturesCount : 0;

  return { token, abonneId, totalAbonnes, totalFactures };
}

// ─── Scénario : un « aller sur l'appli » complet par itération ──────────────

export default function (data) {
  const { token, abonneId, totalAbonnes, totalFactures } = data;

  // 1. Liste paginée des abonnés (page aléatoire) + total, comme un tableau
  //    paginé qui affiche « page X / Y » dès le premier rendu.
  const offsetAbonnes = offsetAleatoire(totalAbonnes, TAILLE_PAGE);
  const [resPageAbonnes, resCountAbonnes] = http.batch([
    requeteBatch(token, GET_ABONNES_PAGE, { limit: TAILLE_PAGE, offset: offsetAbonnes }, 'abonnes_page'),
    requeteBatch(token, GET_ABONNES_COUNT, undefined, 'abonnesCount'),
  ]);
  verifier(resPageAbonnes, 'abonnes (page)');
  verifier(resCountAbonnes, 'abonnesCount');
  tempsDeReflexion(1, 2.5);

  // 2. Parcours non destructif et idempotent : consultation d'un abonné,
  //    puis recherche filtrée — remplace toute mutation (voir en-tête).
  const resAbonne = graphql(token, GET_ABONNE, { id: abonneId }, 'getAbonne');
  verifier(resAbonne, 'abonne (détail)');
  tempsDeReflexion(1, 2.5);

  const resRecherche = graphql(token, GET_ABONNES_RECHERCHE, { limit: TAILLE_PAGE, offset: 0 }, 'abonnes_recherche');
  verifier(resRecherche, 'abonnes (recherche filtrée)');
  tempsDeReflexion(1, 2.5);

  // 3. Liste paginée des factures (page aléatoire) + total.
  const offsetFactures = offsetAleatoire(totalFactures, TAILLE_PAGE);
  const [resPageFactures, resCountFactures] = http.batch([
    requeteBatch(token, GET_FACTURES_PAGE, { limit: TAILLE_PAGE, offset: offsetFactures }, 'factures_page'),
    requeteBatch(token, GET_FACTURES_COUNT, undefined, 'facturesCount'),
  ]);
  verifier(resPageFactures, 'factures (page)');
  verifier(resCountFactures, 'facturesCount');
  tempsDeReflexion(1, 2.5);

  // 4. Dashboard reporting — deux requêtes bon marché chargées ensemble à
  //    l'arrivée sur la page, comme un vrai écran de tableau de bord.
  const [resDashboard, resStatsGlobales] = http.batch([
    requeteBatch(token, GET_DASHBOARD, undefined, 'dashboard'),
    requeteBatch(token, GET_STATS_GLOBALES, undefined, 'statsGlobales'),
  ]);
  verifier(resDashboard, 'dashboard');
  verifier(resStatsGlobales, 'statsGlobales');

  // Fin de « session » — pause plus longue avant qu'un VU ne reprenne un
  // nouveau parcours, comme un utilisateur qui passe à autre chose.
  tempsDeReflexion(2, 4);
}
