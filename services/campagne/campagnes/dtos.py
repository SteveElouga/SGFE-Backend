"""Structures de données légères échangées entre services.py et serializers.py.

Ces dicts ne correspondent à aucun modèle Django (agrégats calculés à la
volée par `CampagneService.list_agents_campagne`) — un TypedDict leur donne
une forme précise sans recourir à `Any` ni à `dict` non paramétré.
"""

from datetime import datetime
from decimal import Decimal
from typing import Optional, TypedDict


class ZoneAgentDict(TypedDict):
    quartier: str
    camp: Optional[int]
    nb_releves: int


class AgentAffecteDict(TypedDict):
    agent_id: str
    zones: list[ZoneAgentDict]
    nb_releves: int
    derniere_activite: Optional[datetime]


class StatsReportingDict(TypedDict):
    nom_campagne: str
    total_abonnes: int
    nb_releves: int
    consommation_totale: Decimal
