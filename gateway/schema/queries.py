import strawberry

from schema.abonne_queries import AbonneQueries
from schema.auth_queries import AuthQueries
from schema.campagne_queries import CampagneQueries
from schema.config_queries import ConfigQueries
from schema.facturation_queries import FacturationQueries
from schema.notification_queries import NotificationQueries
from schema.paiement_queries import PaiementQueries


@strawberry.type
class Query(
    AuthQueries,
    AbonneQueries,
    CampagneQueries,
    ConfigQueries,
    FacturationQueries,
    PaiementQueries,
    NotificationQueries,
):
    pass
