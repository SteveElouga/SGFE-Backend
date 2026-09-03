"""Structures de données légères échangées entre services.py, serializers.py
et grpc_server.py.

Ces dicts ne correspondent à aucun modèle Django — un TypedDict leur donne une
forme précise sans recourir à `Any` ni à `dict` non paramétré (voir
services/campagne/campagnes/dtos.py pour le même motif).
"""

from typing import TypedDict


class UserPayloadDict(TypedDict):
    user_id: str
    username: str
    email: str
    phone_number: str
    role: str
    is_active: bool


class UserResponseDict(UserPayloadDict):
    created_at: str
