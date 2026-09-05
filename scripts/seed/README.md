# Seed de démo — SGFE

Jeu de données de test **multi-mois** pour exercer le dashboard et la query
`statsParMois` (encaissé/facturé par mois, scope par rôle). Écrit directement au
niveau ORM de chaque service (via `manage.py shell`) car `Facture.date_generation`
est en `auto_now_add` — impossible de backdater des factures via l'API gateway.

## Lancer

Prérequis : la stack tourne (`docker compose up -d`, migrations passées).

```bash
bash scripts/seed_demo.sh
```

Idempotent (UUID `uuid5` déterministes) : ré-exécutable sans doublon.

## Comptes créés

Mot de passe commun : **`Demo1234!`** — connexion par `username` (`identifier`).

| username | rôle |
|---|---|
| `demo_admin` | ADMIN |
| `demo_comptable` | COMPTABLE |
| `demo_superviseur` | SUPERVISEUR |
| `demo_agent` | AGENT |

## Abonnés

6 abonnés (`scripts/seed/abonne.py`), statuts variés :

| numéro | nom | statut | zone |
|---|---|---|---|
| `AB-9001` | Mballa Jean | ACTIF | Nkolbisson, camp 12 — rattaché à la campagne Delta (relevé A_RELEVER) |
| `AB-9002` | Etoundi Marie | ACTIF | Nkolbisson, camp 12 — rattaché à la campagne Delta (relevé A_RELEVER) |
| `AB-9003` | Ngono Paul | ACTIF | Mvog-Ada, camp 4 — réservé à la recherche e2e (voir plus bas) |
| `AB-9004` | Fouda Alice | SUSPENDU | Mvog-Ada, camp 4 |
| `AB-9005` | Biya Eric | RESILIE | Essos, camp 7 (compteur désactivé) |
| `AB-9006` | Nkolo Sarah | ACTIF | Essos, camp 7 |

Numérotation en plage réservée (`AB-90xx`/`900xx`) : ne collisionne jamais
avec la suite naturelle (`AB-0001…`) que l'API attribue aux créations
normales sur un environnement déjà peuplé.

## Données (mois relatifs à la date d'exécution ; M0 = mois courant)

4 campagnes :

| campagne | statut | mois | created_by |
|---|---|---|---|
| Démo Alpha | CLOTUREE | M-2 | superviseur |
| Démo Beta | CLOTUREE | M-1 | superviseur |
| Démo Gamma | CLOTUREE | M0 | admin |
| Démo Delta (en cours — terrain) | EN_COURS | M0 | superviseur, `demo_agent` affecté |

Delta porte 2 relevés `A_RELEVER` (abonnés `AB-9001`/`AB-9002`) — nécessaire à
`terrain-saisie-index.spec.ts` (frontend), qui a besoin d'une campagne
EN_COURS avec au moins un abonné « à relever » dans la tournée de l'AGENT
connecté. Elle n'a ni facture ni paiement : sans effet sur les valeurs
`statsParMois` ci-dessous (calculées uniquement à partir de Facture/Paiement
par campagne, jamais du nombre de campagnes).

5 factures backdatées + 4 paiements, dont **pay-1 dissocié** (encaissé en M0 pour
une facture générée en M-2) et **pay-4 annulé** (doit être exclu de l'encaissé).
Les soldes IMPAYES (`fact-a2`, `fact-c2`) reçoivent une `date_limite_paiement`
toujours dans le passé (`date.today() - 10 jours`, voir `scripts/seed/paiement.py`) —
et non plus le 15 du mois courant, qui rendait `/impayes` vide avant le 16 de
chaque mois (bug corrigé, voir le commentaire de `date_limite()` dans ce
script).

## Variables e2e recommandées (`E2E_LIVE_BACKEND=1`, dépôt frontend)

Valeurs à poser une fois ce seed exécuté (voir `SGFE-frontend/e2e/README.md`) :

```bash
E2E_AGENT_USER=demo_agent
E2E_AGENT_PASSWORD='Demo1234!'
E2E_ADMIN_USER=demo_admin
E2E_ADMIN_PASSWORD='Demo1234!'
E2E_COMPTABLE_USER=demo_comptable
E2E_COMPTABLE_PASSWORD='Demo1234!'
E2E_ABONNE_RECHERCHE=AB-9003
```

## Valeurs attendues de `statsParMois(nbMois: 3)` — sur base vide

**SUPERVISEUR** (`demo_superviseur` — Alpha + Beta) :

| mois | encaisse | facture | conso | nbPaiements | nbFactures |
|---|--:|--:|--:|:-:|:-:|
| M0  | 12000 | 0     | 0  | 1 | 0 |
| M-1 | 15000 | 15000 | 30 | 1 | 1 |
| M-2 | 0     | 20000 | 40 | 0 | 2 |

**ADMIN / COMPTABLE** (toutes campagnes) :

| mois | encaisse | facture | conso | nbPaiements | nbFactures |
|---|--:|--:|--:|:-:|:-:|
| M0  | 32000 | 25000 | 50 | 2 | 2 |
| M-1 | 15000 | 15000 | 30 | 1 | 1 |
| M-2 | 0     | 20000 | 40 | 0 | 2 |

> L'ADMIN agrège **toutes** les campagnes : si la base contient déjà d'autres
> données (backup, tests antérieurs), ses totaux dépasseront la table ci-dessus
> (c'est correct). Le SUPERVISEUR reste isolé à ses campagnes → valeurs exactes.
> Pour des chiffres admin identiques à la table : repartir de bases vides
> (`docker compose down -v && docker compose up -d`) avant de reseeder.

## Tester (curl)

```bash
# -k : le nginx local sert un certificat auto-signé de dev (voir CLAUDE.md
# racine, § Frontend — proxy vers la Gateway) ; -L n'est pas nécessaire ici,
# le port HTTPS publié est appelé directement.
TOKEN=$(curl -sk -X POST https://localhost:8443/graphql -H 'Content-Type: application/json' \
  -d '{"query":"mutation{login(identifier:\"demo_superviseur\",password:\"Demo1234!\"){accessToken}}"}' \
  | python3 -c 'import sys,json;print(json.load(sys.stdin)["data"]["login"]["accessToken"])')
curl -sk -X POST https://localhost:8443/graphql -H "Authorization: Bearer $TOKEN" \
  -H 'Content-Type: application/json' \
  -d '{"query":"query{statsParMois(nbMois:3){mois encaisse facture consommation nbPaiements nbFactures}}"}' \
  | python3 -m json.tool
```

> `/dashboard` (front) est réservé ADMIN/COMPTABLE ; un SUPERVISEUR y est redirigé
> vers `/campagnes`. Le scope se teste donc via GraphQL/curl, ou en élargissant
> temporairement le guard `app.routes.ts`. GraphiQL en navigateur est bloqué par
> la CSP nginx — utiliser curl ou un client natif (Altair/Insomnia).
