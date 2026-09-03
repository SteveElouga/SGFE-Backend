from typing import Any
from enum import Enum

import strawberry


@strawberry.enum
class Role(Enum):
    ADMIN = "ADMIN"
    AGENT = "AGENT"
    COMPTABLE = "COMPTABLE"
    SUPERVISEUR = "SUPERVISEUR"


@strawberry.type
class User:
    id: strawberry.ID
    username: str
    email: str
    phone_number: str
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


@strawberry.type
class OtpSentPayload:
    """Retourné par requestPhoneOtp — confirme l'envoi sans révéler si le numéro existe."""

    # Numéro masqué dérivé de l'input, ex. "+237 6•• ••• •78"
    masked_phone: str


def _mask_phone(phone: str) -> str:
    """Masque un numéro camerounais : +2376XXXXXXXX → +237 6•• ••• •XX (2 derniers chiffres visibles)."""
    return f"+237 {phone[4]}{'•' * 2} {'•' * 3} {'•'}{phone[-2:]}"


def user_from_grpc(user_response: Any) -> User:
    """Construit un type GraphQL `User` depuis un `UserResponse`/`UserPayload` gRPC."""
    return User(
        id=strawberry.ID(user_response.user_id),
        username=user_response.username,
        email=user_response.email or "",
        phone_number=user_response.phone_number,
        role=Role(user_response.role),
        is_active=user_response.is_active,
        created_at=getattr(user_response, "created_at", ""),
    )
