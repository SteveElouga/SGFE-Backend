# CLAUDE.md — Config Service

Contexte spécifique à ce service. Voir le `CLAUDE.md` racine pour les règles globales du projet.

## Rôle

Paramètres système partagés (EF-CONF-001, EF-CONF-002). Consommé par Facturation, Paiement et Notification pour lire les délais, les clés API, et les infos société (PDF).

## Structure

```
services/config/
├── config/          # Projet Django (settings, urls, wsgi)
├── parametres/      # App métier : InfosSociete, ConfigParam
│   └── management/commands/grpc_server.py
├── proto/           # Stubs générés depuis proto/config_service.proto — NE PAS MODIFIER
```

## Spécificités

- **InfosSociete** : singleton (pk=1). Créé automatiquement vide au premier accès.
- **ConfigParam** : clé/valeur texte. Les 10 clés par défaut (`CONFIG_DEFAULTS` dans `models.py`, toutes en minuscule) sont initialisées automatiquement au premier accès via `get_or_default()` / `list_all()`. Les noms de clés doivent correspondre exactement (même casse) à ceux attendus par les `ConfigServiceClient` des services consommateurs — un écart fait échouer `GetConfig` en `NOT_FOUND` et retombe silencieusement sur la valeur par défaut codée en dur côté consommateur (voir ANO-001 dans `docs/ETAT_DU_SYSTEME.md`).
- Ce service n'appelle aucun autre service gRPC.

## Démarrage local

```bash
cd services/config
source .venv/bin/activate
python manage.py migrate
python manage.py grpc_server      # démarre le serveur gRPC sur le port 50058
python manage.py test parametres  # tests (utilisent sqlite en mémoire)
```
