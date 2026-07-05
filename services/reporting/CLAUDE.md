# CLAUDE.md — Reporting Service

Contexte spécifique à ce service. Voir le `CLAUDE.md` racine pour les règles globales du projet.

## Rôle

Agrégateur **read-only** du tableau de bord (ADR-019). C'est le **côté Query d'un pattern CQRS** :
il maintient des tables dénormalisées (`reporting_db`) pré-calculées à la réception d'événements
des autres services, et ne répond qu'à des lectures — jamais de logique métier propre.

## Structure

```
services/reporting/
├── reporting/        # Projet Django (settings, urls, wsgi)
├── stats/            # App métier : StatsCampagne, StatsFacturation, StatsPaiements
│   ├── models.py         # 3 tables dénormalisées (docs/ARCHITECTURE.md §8.7)
│   ├── services.py       # AgregateurDashboard (lectures + mises à jour idempotentes)
│   ├── repositories.py   # accès BD (upsert par campagne_id)
│   ├── grpc_server.py    # Servicer ReportingService (port 50057)
│   └── management/commands/grpc_server.py
├── proto/            # Stubs générés depuis proto/reporting_service.proto — NE PAS MODIFIER
```

## Spécificités

- **Lectures** : `GetDashboard` (campagne la plus récemment mise à jour = « en cours »),
  `GetStatsCampagne`, `GetStatsGlobales`.
- **Mises à jour** (déclenchées par les événements amont) : `UpdateStatsCampagne`,
  `UpdateStatsFacturation` (type_update = GENEREE/ENVOYEE/PAYEE), `UpdateStatsPaiements`
  (type_update = PAIEMENT/IMPAYE_RESOLU). Upsert par `campagne_id`, idempotent autant que possible.
- Ce service **n'appelle aucun autre service gRPC** (pas de `grpc_clients.py`) — il ne fait que
  recevoir des poussées de stats et les relire.

## ⚠️ Câblage événementiel — À FAIRE

Le service expose bien les RPC `UpdateStats*`, mais **personne ne les appelle encore**. Pour
alimenter le tableau de bord, il reste à câbler les émetteurs (PR séparées, service par service) :
- **campagne-service** : à la clôture / progression → `UpdateStatsCampagne`
- **facturation-service** : à `GenererFactures` (GENEREE), envoi WhatsApp (ENVOYEE), passage PAYEE
  → `UpdateStatsFacturation`
- **paiement-service** : à `EnregistrerPaiement` (PAIEMENT) et résolution d'impayé (IMPAYE_RESOLU)
  → `UpdateStatsPaiements`

Tant que ce câblage n'est pas fait, `dashboard` renvoie des sous-blocs nuls (dégradation propre).

## Démarrage local

```bash
cd services/reporting
python -m venv .venv && source .venv/bin/activate && pip install -r requirements.txt
python manage.py migrate
python manage.py grpc_server      # démarre le serveur gRPC sur le port 50057
python manage.py test stats       # tests (utilisent sqlite en mémoire)
```

## Génération des stubs proto

```bash
python -m grpc_tools.protoc -I ../../proto/ \
  --python_out=proto/ --grpc_python_out=proto/ \
  ../../proto/reporting_service.proto
```
