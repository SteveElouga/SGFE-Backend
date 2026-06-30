import strawberry

from schema.extensions import GrpcErrorExtension
from schema.mutations import Mutation
from schema.queries import Query
from schema.subscriptions import Subscription

schema = strawberry.Schema(
    query=Query,
    mutation=Mutation,
    subscription=Subscription,
    extensions=[GrpcErrorExtension],
)
