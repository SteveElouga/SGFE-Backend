# CLAUDE.md — Campagne Service

Contexte spécifique à ce service. Voir le `CLAUDE.md` racine pour les règles globales du projet.

## Rôle

Gestion des campagnes de relevé mensuelle et des relevés d'index (EF-CAMP-001 à EF-CAMP-007).

## Structure

```
services/campagne/
├── campagne/          # Projet Django (settings, urls, wsgi)
├── campagnes/         # App métier : Campagne, Releve
│   ├── management/commands/grpc_server.py
│   └── schedulers.py  # APScheduler cron 7h00
├── proto/             # Stubs générés depuis proto/campagne_service.proto — NE PAS MODIFIER
```

## Spécificités

- **Campagne** : cycle de vie PLANIFIEE → EN_COURS → CLOTUREE.
- **Releve** : unique par (campagne, abonne_id). Créé automatiquement lors de `SaisirIndex` si inexistant.
- **CampagneCloturee** : à la clôture, Campagne Service notifie Facturation Service via gRPC.
- **Cron 7h00** : démarre les campagnes planifiées pour J ou J-1 (rattrapage).
- **Filtrage SUPERVISEUR** : `ListCampagnes(created_by)` — filtre au niveau service.

## Démarrage local

```bash
cd services/campagne
source .venv/bin/activate
python manage.py migrate
python manage.py grpc_server      # démarre le serveur gRPC sur le port 50053
python manage.py test campagnes   # tests (utilisent sqlite en mémoire)
```
