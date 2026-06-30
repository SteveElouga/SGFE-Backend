import strawberry

from schema.abonne_mutations import AbonneMutations
from schema.auth_mutations import AuthMutations
from schema.config_mutations import ConfigMutations


@strawberry.type
class Mutation(AuthMutations, AbonneMutations, ConfigMutations):
    pass
