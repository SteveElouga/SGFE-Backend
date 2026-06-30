import strawberry

from schema.abonne_queries import AbonneQueries
from schema.auth_queries import AuthQueries
from schema.config_queries import ConfigQueries


@strawberry.type
class Query(AuthQueries, AbonneQueries, ConfigQueries):
    pass
