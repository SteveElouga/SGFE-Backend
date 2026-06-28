import strawberry

from schema.abonne_mutations import AbonneMutations
from schema.auth_mutations import AuthMutations


@strawberry.type
class Mutation(AuthMutations, AbonneMutations):
    pass
