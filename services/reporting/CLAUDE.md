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
  `GetStatsCampagne`, `GetStatsCompletes` (stats des 3 domaines pour une campagne précise,
  sous-blocs `None` si inconnue — utilisé par la synthèse PDF facturation), `GetStatsGlobales`.
- **Mises à jour** (déclenchées par les événements amont) : `UpdateStatsCampagne`,
  `UpdateStatsFacturation` (type_update = GENEREE/ENVOYEE/PAYEE/ANNULEE), `UpdateStatsPaiements`
  (type_update = PAIEMENT/PAIEMENT_ANNULE/IMPAYE_RESOLU). Upsert par `campagne_id`, idempotent
  autant que possible.
- Ce service reçoit des poussées de stats en continu (événements Redis, voie passive) mais appelle
  aussi Facturation Service et Paiement Service en gRPC pour une réconciliation nocturne
  (`stats/grpc_clients.py`, `stats/schedulers.py`, cron 3h00, voir `ReconciliateurStats`) qui
  corrige la dérive d'un événement jamais publié.

## Câblage événementiel (fait)

campagne-service, facturation-service et paiement-service publient sur le flux Redis Streams
`reporting:stream` via `publish_reporting_event` ; `stats/event_consumer.py` les consomme de façon
idempotente (consumer group Redis, idempotence via le modèle `ProcessedEvent`, dead-letter après
5 tentatives) et est démarré automatiquement dans `stats/grpc_server.py::serve()`. Une
réconciliation nocturne (`stats/schedulers.py`, 3h00, verrou PostgreSQL) corrige la dérive
résiduelle si un événement n'a jamais été publié.

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
