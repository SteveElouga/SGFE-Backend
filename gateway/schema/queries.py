import strawberry

from schema.abonne_queries import AbonneQueries
from schema.auth_queries import AuthQueries
from schema.campagne_queries import CampagneQueries
from schema.config_queries import ConfigQueries
from schema.facturation_queries import FacturationQueries


@strawberry.type
class Query(AuthQueries, AbonneQueries, CampagneQueries, ConfigQueries, FacturationQueries):
    pass
