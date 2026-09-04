"""Interface pluggable vers un fournisseur de paiement en ligne.

Paiement en ligne dans l'espace abonné — relance de la décision §10.2 de
l'audit, qui l'avait initialement écartée (« consultation seule, paiement en
ligne reporté »). Implémenté ici en mode **sandbox/mock exclusivement** :
aucune vraie passerelle de paiement n'est contactée, et aucun identifiant de
fournisseur réel n'existe dans ce projet.

`PasserellePaiementClient` est le point d'extension : un futur fournisseur
réel (Mobile Money, agrégateur bancaire, etc.) n'a qu'à implémenter cette
interface et être branché à la place de `MockPasserellePaiementClient` — sans
autre changement de code applicatif (ni dans `services.py`, ni dans
`grpc_server.py`).
"""

from abc import ABC, abstractmethod
from decimal import Decimal

from django.conf import settings


class PasserellePaiementClient(ABC):
    """Interface d'un fournisseur de paiement en ligne (mock aujourd'hui, réel demain)."""

    @abstractmethod
    def creer_session(self, montant: Decimal, reference: str) -> str:
        """Ouvre une session de paiement chez le fournisseur.

        `reference` est la référence de transaction que porte notre système
        (le `session_id`, qui devient `reference_transaction` du `Paiement` à
        la confirmation) — le fournisseur la reçoit pour pouvoir la restituer
        à la confirmation.

        Rend l'URL vers laquelle rediriger l'abonné pour payer.
        """
        raise NotImplementedError

    @abstractmethod
    def confirmer(self, reference: str) -> bool:
        """Interroge le fournisseur sur l'issue du paiement `reference`.

        Rend `True` si le paiement a réellement abouti chez le fournisseur,
        `False` sinon (échec, refus, transaction inconnue).
        """
        raise NotImplementedError


class MockPasserellePaiementClient(PasserellePaiementClient):
    """MOCK de développement — ne JAMAIS activer en production sans le
    remplacer par une vraie implémentation d'un fournisseur réel (Mobile
    Money, agrégateur, etc.).

    C'est aujourd'hui la SEULE implémentation de `PasserellePaiementClient` —
    active par défaut, sans variable d'environnement pour la sélectionner, car
    il n'en existe pas d'autre. Elle ne contacte jamais de service externe :
    `creer_session` renvoie directement l'URL du mock de confirmation côté
    frontend (`/espace/<token>/paiement/<session_id>/confirmer`), et
    `confirmer` renvoie toujours `True` — il n'y a pas de vraie règle de
    passerelle à simuler, seulement un point d'extension pour le jour où il y
    en aura une.

    `token_espace` n'est nécessaire qu'à CE mock, pas à l'interface générale :
    il simule la redirection vers l'espace abonné du frontend, qui a besoin du
    token dans son URL pour pouvoir ensuite appeler la confirmation. Un vrai
    fournisseur n'en aurait aucun besoin — il redirigerait vers sa propre
    page de paiement, avec ses propres identifiants d'API.
    """

    def __init__(self, token_espace: str) -> None:
        """Construit le mock pour LA session en cours, identifiée par son token."""
        self._token_espace = token_espace

    def creer_session(self, montant: Decimal, reference: str) -> str:
        """Renvoie directement l'URL frontend du mock — aucune passerelle réelle appelée.

        `montant` n'est pas utilisé par le mock (rien à transmettre à un
        fournisseur qui n'existe pas) ; il reste dans la signature parce que
        l'interface générale en a besoin pour un vrai fournisseur.
        """
        return f"{settings.FRONTEND_URL}/espace/{self._token_espace}/paiement/{reference}/confirmer"

    def confirmer(self, reference: str) -> bool:
        """Toujours `True` : aucune vraie règle de passerelle à simuler ici."""
        return True
