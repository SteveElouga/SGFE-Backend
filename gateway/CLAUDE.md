# CLAUDE.md — API Gateway

Contexte spécifique à ce service. Voir le `CLAUDE.md` racine pour les règles globales du projet.

## Rôle

Point d'entrée unique pour le frontend (Angular). Expose un schéma GraphQL (Strawberry, ASGI) qui fédère les appels gRPC vers les microservices. **Aucune base de données.**

## Structure

```
gateway/
├── gateway/         # Projet Django ASGI (settings, urls, asgi)
├── schema/          # App Django : types, queries, mutations, grpc_clients, context (JWT)
│   ├── *_types.py       # Types Strawberry par domaine (auth_types.py, abonne_types.py, ...)
│   ├── *_queries.py / *_mutations.py  # Resolvers par domaine, agrégés dans queries.py/mutations.py
│   ├── grpc_clients.py  # Clients gRPC vers les microservices (auth_client, abonne_client, ...)
│   ├── context.py       # Extraction du JWT (header Authorization) + require_auth/require_role
│   ├── espace_abonne.py # Vues Django (pas GraphQL) pour l'espace abonné public tokenisé
│   ├── subscriptions.py # Subscriptions GraphQL (Redis pub/sub)
│   └── schema.py        # strawberry.Schema(query=Query, mutation=Mutation, subscription=Subscription)
├── proto/           # Stubs générés — NE PAS MODIFIER
```

## Spécificités

- Tous les domaines sont branchés sauf `reporting` (service pas encore implémenté) : auth, abonne, campagne, facturation, paiement, notification, config ont chacun leurs `grpc_client` + resolvers (`*_queries.py`/`*_mutations.py`).
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
