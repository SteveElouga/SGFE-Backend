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

## Données (mois relatifs à la date d'exécution ; M0 = mois courant)

3 campagnes :

| campagne | mois | created_by |
|---|---|---|
| Démo Alpha | M-2 | superviseur |
| Démo Beta | M-1 | superviseur |
| Démo Gamma | M0 | admin |

5 factures backdatées + 4 paiements, dont **pay-1 dissocié** (encaissé en M0 pour
une facture générée en M-2) et **pay-4 annulé** (doit être exclu de l'encaissé).

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
