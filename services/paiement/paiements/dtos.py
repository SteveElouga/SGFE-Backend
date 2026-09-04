"""Structures de données légères échangées entre grpc_clients.py, services.py
et event_publisher.py.

Ces dicts ne correspondent à aucun modèle Django — un TypedDict leur donne une
forme précise sans recourir à `Any` ni à `dict` non paramétré.
"""

from datetime import date, datetime
from decimal import Decimal
from typing import Protocol, TypedDict


class DelaisImpayesDict(TypedDict):
    rappel_1: int
    rappel_2: int
    avertissement: int
    suspension: int
    suspension_auto: bool
    suspension_relances: int


class TokenValideDict(TypedDict):
    """Résultat de `NotificationServiceClient.valider_token` — voir
    `paiements/grpc_clients.py`. Un TypedDict plutôt que le message protobuf
    brut : cohérent avec `DelaisImpayesDict` ci-dessus, et évite d'exposer un
    type dont l'import est lazy (voir `_ensure_proto_in_syspath`)."""

    is_valid: bool
    abonne_id: str


class PaiementEventSource(Protocol):
    """Forme structurelle attendue par `publish_paiement_event` (event_publisher.py).

    Un Protocol plutôt qu'une dépendance directe au modèle `Paiement` : les
    doubles de test (`SimpleNamespace`) n'en héritent pas mais y correspondent
    structurellement, ce que `publish_paiement_event` peut donc accepter sans
    recourir à `Any`. Membres exposés en propriétés (lecture seule) plutôt
    qu'en attributs : `publish_paiement_event` ne fait que les lire, et la
    variance covariante d'une propriété laisse `Paiement.id` (UUID) et le
    double de test (`id: str`) satisfaire tous deux `-> object`.
    """

    @property
    def id(self) -> object: ...
    @property
    def facture_id(self) -> str: ...
    @property
    def montant(self) -> Decimal: ...
    @property
    def date_paiement(self) -> date: ...
    @property
    def mode_paiement(self) -> str: ...
    @property
    def reference_transaction(self) -> str: ...
    @property
    def created_at(self) -> datetime: ...
    @property
    def enregistre_par(self) -> str: ...
