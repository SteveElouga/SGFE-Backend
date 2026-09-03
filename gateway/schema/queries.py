import strawberry

from schema.abonne_queries import AbonneQueries
from schema.auth_queries import AuthQueries
from schema.campagne_queries import CampagneQueries
from schema.communication_queries import CommunicationQueries
from schema.config_queries import ConfigQueries
from schema.facturation_queries import FacturationQueries
from schema.notification_queries import NotificationQueries
from schema.paiement_queries import PaiementQueries
from schema.reporting_queries import ReportingQueries
from schema.stats_queries import StatsQueries


@strawberry.type
class Query(
    AuthQueries,
    AbonneQueries,
    CampagneQueries,
    ConfigQueries,
    FacturationQueries,
    PaiementQueries,
    NotificationQueries,
    CommunicationQueries,
    ReportingQueries,
    StatsQueries,
):
    pass
