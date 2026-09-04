"""Contexte d'identité de la requête gateway courante.

Voir AUDIT_SGFE.md §10.7 pour la conception complète (propagation d'identité
→ journal d'audit immuable). `require_auth` (context.py) pose l'identité de
l'utilisateur authentifié ici, dans un `ContextVar` — pas dans un paramètre
supplémentaire passé de resolver en resolver. `IdentityClientInterceptor`
(grpc_clients.py) la relit au moment d'émettre chaque appel gRPC sortant pour
poser les métadonnées `x-user-id`/`x-user-name`/`x-user-role`/`x-request-id`.

Aucun champ identité n'est ajouté aux messages gRPC eux-mêmes (décision 2 de
la conception) : les champs ad hoc existants (`created_by`, `auteur_*`,
`caller_id`, `enregistre_par`...) restent en place tels quels pour cette
étape — zéro régression, zéro changement fonctionnel.

Point de vigilance (voir §10.7) : un `ContextVar` ne franchit PAS la
frontière d'un `asyncio.to_thread`/`loop.run_in_executor` sans y être
explicitement recopié — `contextvars.copy_context()` fige une photographie
au moment de l'appel, et toute mutation faite à l'intérieur (un `.set()`)
reste locale à cette photographie : elle ne remonte jamais vers la coroutine
appelante ni vers un `to_thread` ultérieur. Les resolvers de requêtes/mutations
de ce module (fonctions synchrones appelées directement par l'exécuteur
GraphQL, sans changement de thread) ne sont pas concernés : `set_identity`
puis l'appel gRPC qui suit s'exécutent dans le même contexte. Les
souscriptions (`subscriptions.py`), elles, appellent `require_auth`/
`require_role` puis les résolutions gRPC ultérieures via des `asyncio.
to_thread(...)` **séparés** : l'identité posée par le premier ne survit pas
jusqu'aux suivants. Vérifié par les tests de ce module
(`tests/test_identity_context.py`) — c'est un point d'attention documenté,
pas un défaut de ce module : les souscriptions ne portent que des lectures,
hors du périmètre du journal d'audit (écritures uniquement).
"""

from __future__ import annotations

import uuid
from collections.abc import Awaitable, Callable
from contextvars import ContextVar
from dataclasses import dataclass

from asgiref.sync import iscoroutinefunction, markcoroutinefunction
from django.http import HttpRequest, HttpResponse


@dataclass(frozen=True)
class Identity:
    """Identité de l'utilisateur authentifié pour la requête GraphQL courante."""

    user_id: str
    username: str
    role: str


# `None` = appel anonyme (login, refresh, espace abonné public, OTP...) : aucune
# identité à propager, comportement inchangé pour ces parcours.
current_identity: ContextVar[Identity | None] = ContextVar("current_identity", default=None)

# Identifiant de corrélation de la requête courante, posé en même temps que
# l'identité (une seule fois par requête). Ne sert qu'à corréler les logs
# entre services pour une même requête gateway — pas un `trace_id`
# d'observabilité complet (item séparé, hors périmètre ici).
current_request_id: ContextVar[str | None] = ContextVar("current_request_id", default=None)


def set_identity(user_id: str, username: str, role: str) -> None:
    """Pose l'identité de la requête courante (appelé par `require_auth` après validation du JWT).

    Génère au passage un identifiant de corrélation si aucun n'existe encore
    pour cette requête (idempotent : un second appel dans la même requête ne
    le régénère pas).
    """
    current_identity.set(Identity(user_id=user_id, username=username, role=role))
    if current_request_id.get() is None:
        current_request_id.set(str(uuid.uuid4()))


def get_identity() -> Identity | None:
    """Renvoie l'identité de la requête courante, ou `None` pour un appel anonyme."""
    return current_identity.get()


def get_request_id() -> str:
    """Renvoie l'identifiant de corrélation de la requête courante.

    N'est appelé par `IdentityClientInterceptor` que lorsqu'une identité est
    déjà posée (voir `set_identity`) : cette fonction ne devrait donc jamais
    avoir à en générer un elle-même, mais reste défensive (un identifiant
    plutôt qu'une valeur vide) si elle était un jour appelée hors de ce cas.
    """
    request_id = current_request_id.get()
    if request_id is None:
        request_id = str(uuid.uuid4())
        current_request_id.set(request_id)
    return request_id


def reset_identity() -> None:
    """Réinitialise l'identité et l'identifiant de corrélation courants.

    À appeler en tout début de traitement de CHAQUE requête (voir
    `ResetIdentityMiddleware` ci-dessous), avant même l'authentification.

    Nécessaire malgré la conception « un ContextVar par requête » : en ASGI,
    chaque requête démarre en principe dans sa propre tâche asyncio, avec un
    contexte copié et donc isolé des autres — mais un worker qui rejoue
    plusieurs requêtes sur le même thread sans cette frontière (constaté avec
    le test runner Django, qui exécute toute la suite dans un seul processus
    et un seul thread synchrone, sans tâche asyncio entre deux tests) laisse
    sinon l'identité d'une requête « fuiter » vers la suivante. Un appel
    anonyme qui suivrait un appel authentifié sur le même thread/contexte
    hériterait alors à tort d'une identité qui n'est pas la sienne.
    """
    current_identity.set(None)
    current_request_id.set(None)


class ResetIdentityMiddleware:
    """Middleware Django (sync et async) qui réinitialise l'identité de requête.

    Posé en tête de `MIDDLEWARE` (voir `settings.py`) pour s'exécuter avant
    toute authentification et tout resolver GraphQL — voir `reset_identity`
    pour le rationale. Ne couvre que les requêtes routées par Django
    (`_django_app` dans `asgi.py`) ; les souscriptions WebSocket
    (`strawberry.asgi.GraphQL`, montées à côté dans `asgi.py`) n'en ont pas
    besoin : chaque connexion y est déjà une tâche asyncio indépendante, et
    l'identité n'y est de toute façon jamais conservée d'un `asyncio.to_thread`
    à l'autre (voir la docstring de ce module).
    """

    async_capable = True
    sync_capable = True

    def __init__(self, get_response: Callable[[HttpRequest], HttpResponse | Awaitable[HttpResponse]]) -> None:
        self.get_response = get_response
        # Motif officiel Django (« Supporting both sync and async middleware ») :
        # se déclare coroutine si la suite de la chaîne l'est, pour que
        # `BaseHandler` construise une chaîne entièrement asynchrone sans
        # aller-retour thread superflu (la gateway est ASGI de bout en bout).
        if iscoroutinefunction(self.get_response):
            markcoroutinefunction(self)

    def __call__(self, request: HttpRequest) -> HttpResponse | Awaitable[HttpResponse]:
        if iscoroutinefunction(self.get_response):
            return self.__acall__(request)
        reset_identity()
        response = self.get_response(request)
        assert not isinstance(response, Awaitable)  # get_response sync ici (branche non-coroutine)
        return response

    async def __acall__(self, request: HttpRequest) -> HttpResponse:
        reset_identity()
        response = self.get_response(request)
        assert isinstance(response, Awaitable)  # get_response coroutine ici (branche coroutine)
        return await response
