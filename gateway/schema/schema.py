import strawberry

from schema.mutations import Mutation
from schema.queries import Query

schema = strawberry.Schema(query=Query, mutation=Mutation)
