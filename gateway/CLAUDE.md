# CLAUDE.md — API Gateway

Contexte spécifique à ce service. Voir le `CLAUDE.md` racine pour les règles globales du projet.

## Rôle

Point d'entrée unique pour le frontend (Angular). Expose un schéma GraphQL (Strawberry, ASGI) qui fédère les appels gRPC vers les microservices. **Aucune base de données.**

## Structure

```
gateway/
├── gateway/         # Projet Django ASGI (settings, urls, asgi)
├── schema/          # App Django : types, queries, mutations, grpc_clients, context (JWT)
│   ├── types.py        # Types Strawberry (miroir du schéma GraphQL — docs/ARCHITECTURE.md §10)
│   ├── grpc_clients.py  # Clients gRPC vers les microservices (auth_client, ...)
│   ├── context.py       # Extraction du JWT (header Authorization) + require_auth/require_role
│   ├── queries.py / mutations.py
│   └── schema.py        # strawberry.Schema(query=Query, mutation=Mutation)
├── proto/           # Stubs générés — NE PAS MODIFIER
```

## Spécificités

- Seul `auth_service` est branché pour l'instant (login, refreshToken, logout, me, createUser, deactivateUser). Les autres types du schéma (Abonne, Campagne, Facture, ...) ne sont pas encore résolus — ajouter leur `grpc_client` + resolver au fur et à mesure que chaque microservice est implémenté.
- `require_auth`/`require_role` (dans `context.py`) valident le JWT en appelant `AuthService.ValidateToken` en gRPC à chaque requête protégée (pas de décodage JWT local — la source de vérité reste `auth_service`, qui peut révoquer un token).
- GraphiQL activé sur `/graphql` (`graphiql=True`), CSRF désactivé sur cette route (API stateless, pas de session Django).

## Démarrage local

```bash
cd gateway
source .venv/bin/activate
python manage.py test schema     # tests (auth_client mocké, pas besoin du service auth réel)
python manage.py runserver 8000  # ou: daphne -b 0.0.0.0 -p 8000 gateway.asgi:application
# GraphiQL : http://localhost:8000/graphql (nécessite auth-service démarré pour tester réellement)
```
