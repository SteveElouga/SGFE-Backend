import strawberry

from schema.abonne_queries import AbonneQueries
from schema.auth_queries import AuthQueries


@strawberry.type
class Query(AuthQueries, AbonneQueries):
    pass
