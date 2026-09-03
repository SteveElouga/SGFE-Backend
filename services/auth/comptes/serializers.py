from comptes.dtos import UserPayloadDict, UserResponseDict
from comptes.models import User


def user_to_payload(user: User) -> UserPayloadDict:
    """Sérialise un User vers les champs de `UserPayload` (auth_service.proto)."""
    return {
        "user_id": str(user.id),
        "username": user.username,
        "email": user.email or "",
        "phone_number": user.phone_number,
        "role": user.role,
        "is_active": user.is_active,
    }


def user_to_response(user: User) -> UserResponseDict:
    """Sérialise un User vers les champs de `UserResponse` (auth_service.proto)."""
    return {
        "user_id": str(user.id),
        "username": user.username,
        "email": user.email or "",
        "phone_number": user.phone_number,
        "role": user.role,
        "is_active": user.is_active,
        "created_at": user.created_at.isoformat(),
    }
