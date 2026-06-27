# CLAUDE.md — Auth Service

Contexte spécifique à ce service. Voir le `CLAUDE.md` racine pour les règles globales du projet.

## Rôle

Authentification (JWT) et gestion des utilisateurs/rôles (ADMIN, AGENT, COMPTABLE). Premier service du système : tous les autres en dépendent indirectement via la validation de token côté Gateway.

## Structure

```
services/auth/
├── auth/            # Projet Django (settings, urls, wsgi)
├── comptes/         # App métier : User, RevokedToken, services, repositories, grpc_server
│   └── management/commands/grpc_server.py   # `python manage.py grpc_server`
├── proto/           # Stubs générés depuis proto/auth_service.proto — NE PAS MODIFIER
```

## Spécificités

- `AUTH_USER_MODEL = comptes.User` — utilisateur custom (UUID, `role`, `failed_attempts`, `locked_until`).
- JWT généré/validé via `djangorestframework-simplejwt`, utilisé en mode standalone (pas de vues DRF HTTP) — toute l'API est exposée en gRPC.
- Verrouillage de compte après `MAX_LOGIN_ATTEMPTS` échecs, pendant `LOCKOUT_DURATION_MINUTES` (configurable via `.env`).
- Logout = ajout du `jti` du token courant dans `RevokedToken` (blacklist vérifiée à chaque `ValidateToken`/`RefreshToken`).
- Ce service n'appelle aucun autre service gRPC (pas de `grpc_clients.py`).

## Démarrage local

```bash
cd services/auth
source .venv/bin/activate
python manage.py migrate
python manage.py grpc_server      # démarre le serveur gRPC sur le port 50051
python manage.py test comptes     # tests (utilisent sqlite en mémoire)
```
