import strawberry

from schema.abonne_queries import AbonneQueries
from schema.auth_queries import AuthQueries
from schema.campagne_queries import CampagneQueries
from schema.config_queries import ConfigQueries


@strawberry.type
class Query(AuthQueries, AbonneQueries, CampagneQueries, ConfigQueries):
    pass
