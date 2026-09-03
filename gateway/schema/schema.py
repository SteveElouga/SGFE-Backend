import strawberry
from django.conf import settings
from graphql import NoSchemaIntrospectionCustomRule
from strawberry.extensions import (
    AddValidationRules,
    MaxAliasesLimiter,
    MaxTokensLimiter,
    QueryDepthLimiter,
    SchemaExtension,
)

from schema.extensions import GrpcErrorExtension
from schema.mutations import Mutation
from schema.queries import Query
from schema.subscriptions import Subscription

# Bornes anti-abus sur le coût des requêtes GraphQL (profondeur, nombre d'alias,
# volume de tokens). Valeurs généreuses pour ne pas gêner les requêtes légitimes
# du frontend ; elles bloquent les requêtes pathologiques (récursion profonde,
# aliasing massif) qui saturent la gateway. Ajustables selon les besoins réels.
_extensions: list[type[SchemaExtension] | SchemaExtension] = [
    GrpcErrorExtension,
    QueryDepthLimiter(max_depth=12),
    MaxAliasesLimiter(max_alias_count=50),
    MaxTokensLimiter(max_token_count=5000),
]
# Hors dev : désactive l'introspection du schéma (réduit la reconnaissance et la
# surface d'attaque). En dev, introspection + GraphiQL restent actifs (outillage).
if not settings.DEBUG:
    _extensions.append(AddValidationRules([NoSchemaIntrospectionCustomRule]))

schema = strawberry.Schema(
    query=Query,
    mutation=Mutation,
    subscription=Subscription,
    extensions=_extensions,
)
