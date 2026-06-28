import strawberry

from schema.extensions import GrpcErrorExtension
from schema.mutations import Mutation
from schema.queries import Query

schema = strawberry.Schema(query=Query, mutation=Mutation, extensions=[GrpcErrorExtension])
