from enum import Enum

import strawberry


@strawberry.enum
class Role(Enum):
    ADMIN = "ADMIN"
    AGENT = "AGENT"
    COMPTABLE = "COMPTABLE"


@strawberry.type
class User:
    id: strawberry.ID
    username: str
    email: str
    role: Role
    is_active: bool
    created_at: str


@strawberry.type
class AuthPayload:
    """Le refresh token n'apparaît jamais ici : il est posé en cookie HttpOnly
    (voir mutations.py), jamais exposé à JS côté client."""

    access_token: str
    expires_in: int
    user: User


def user_from_grpc(user_response) -> User:
    """Construit un type GraphQL `User` depuis un `UserResponse`/`UserPayload` gRPC."""
    return User(
        id=strawberry.ID(user_response.user_id),
        username=user_response.username,
        email=user_response.email,
        role=Role(user_response.role),
        is_active=user_response.is_active,
        created_at=getattr(user_response, "created_at", ""),
    )
