import strawberry

from schema.abonne_mutations import AbonneMutations
from schema.auth_mutations import AuthMutations
from schema.campagne_mutations import CampagneMutations
from schema.config_mutations import ConfigMutations
from schema.facturation_mutations import FacturationMutations
from schema.notification_mutations import NotificationMutations
from schema.paiement_mutations import PaiementMutations


@strawberry.type
class Mutation(
    AuthMutations,
    AbonneMutations,
    CampagneMutations,
    ConfigMutations,
    FacturationMutations,
    PaiementMutations,
    NotificationMutations,
):
    pass
