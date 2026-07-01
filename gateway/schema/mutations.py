import strawberry

from schema.abonne_mutations import AbonneMutations
from schema.auth_mutations import AuthMutations
from schema.campagne_mutations import CampagneMutations
from schema.config_mutations import ConfigMutations
from schema.facturation_mutations import FacturationMutations


@strawberry.type
class Mutation(AuthMutations, AbonneMutations, CampagneMutations, ConfigMutations, FacturationMutations):
    pass
