from __future__ import annotations

import logging
from collections.abc import Iterable
from contextvars import ContextVar
from dataclasses import dataclass
from typing import Any, Callable

import grpc
from django.core.exceptions import ObjectDoesNotExist
from django.db import IntegrityError
from django.utils.translation import gettext_lazy as _

from comptes.email_client import EmailDeliveryError
from comptes.services import AuthenticationError
from comptes.throttle import ThrottleError
from comptes.whatsapp_client import WhatsAppDeliveryError

logger = logging.getLogger(__name__)

# Le 3e élément est le message renvoyé au client : None signifie "utiliser
# str(exc)" (message métier déjà sûr, ex. "Identifiants invalides"). Pour
# IntegrityError/EmailDeliveryError, str(exc) contient un détail interne
# (contrainte SQL, réponse brute du fournisseur d'e-mail) — remplacé par un
# message générique pour ne pas exposer un détail d'implémentation.
#
# ThrottleError -> RESOURCE_EXHAUSTED : code gRPC dédié au dépassement de
# quota/débit (cohérent avec la sémantique HTTP 429), à distinguer d'un
# UNAUTHENTICATED ou d'un INVALID_ARGUMENT — le client sait ainsi qu'il doit
# réessayer plus tard plutôt que corriger sa requête.
_STATUS_BY_EXCEPTION = (
    (AuthenticationError, grpc.StatusCode.UNAUTHENTICATED, None),
    (ObjectDoesNotExist, grpc.StatusCode.NOT_FOUND, None),
    (ThrottleError, grpc.StatusCode.RESOURCE_EXHAUSTED, None),
    (ValueError, grpc.StatusCode.INVALID_ARGUMENT, None),
    (IntegrityError, grpc.StatusCode.ALREADY_EXISTS, _("Cette ressource existe déjà")),
    (EmailDeliveryError, grpc.StatusCode.UNAVAILABLE, _("Échec de l'envoi de l'e-mail, réessayez plus tard")),
    (WhatsAppDeliveryError, grpc.StatusCode.UNAVAILABLE, _("Échec de l'envoi WhatsApp, réessayez plus tard")),
)


def _abort_for(exc: Exception, context: grpc.ServicerContext, handler_call_details: grpc.HandlerCallDetails) -> None:
    """Cherche un mapping pour `exc` et appelle context.abort() (qui lève).
    Si aucun mapping ne correspond, journalise et laisse l'appelant relever
    l'exception d'origine."""
    for exc_type, status_code, message in _STATUS_BY_EXCEPTION:
        if isinstance(exc, exc_type):
            if message:
                # Le détail réel (str(exc)) est masqué au client : gardé dans
                # les logs serveur pour le débogage.
                logger.warning("%s: %s", exc_type.__name__, exc)
            context.abort(status_code, message or str(exc))
            return
    method = getattr(handler_call_details, "method", "?")
    logger.exception("Exception non gérée dans %s", method)


class ErrorHandlingInterceptor(grpc.ServerInterceptor):
    """Convertit les exceptions Django/métier en codes gRPC appropriés.

    Centralise ce mapping une seule fois plutôt que de répéter un
    try/except dans chaque méthode de chaque servicer.
    """

    def intercept_service(
        self,
        continuation: Callable[[grpc.HandlerCallDetails], grpc.RpcMethodHandler[Any, Any] | None],
        handler_call_details: grpc.HandlerCallDetails,
    ) -> grpc.RpcMethodHandler[Any, Any] | None:
        handler = continuation(handler_call_details)
        if handler is None or not handler.unary_unary:
            return handler

        original_behavior = handler.unary_unary

        def wrapped_behavior(request: Any, context: grpc.ServicerContext) -> Any:
            try:
                return original_behavior(request, context)
            except Exception as exc:
                _abort_for(exc, context, handler_call_details)
                raise

        return grpc.unary_unary_rpc_method_handler(
            wrapped_behavior,
            request_deserializer=handler.request_deserializer,
            response_serializer=handler.response_serializer,
        )


# ─────────────────────────────────────────────────────────────────────────
# Identité de l'appelant — propagée par la gateway (voir AUDIT_SGFE.md §10.7,
# « Conception — propagation d'identité → journal d'audit immuable »).
# ─────────────────────────────────────────────────────────────────────────


@dataclass(frozen=True)
class CallerIdentity:
    """Identité de l'appelant, lue depuis les métadonnées gRPC posées par la
    gateway (`IdentityClientInterceptor`, gateway/schema/grpc_clients.py).

    Une identité "vide" (tous les champs à chaîne vide, valeur par défaut de
    ce type) signifie qu'aucune identité n'a été propagée — appel anonyme
    côté gateway (login, espace abonné public...) ou appel de test sans
    métadonnées. `is_anonyme` distingue ce cas sans comparer chaque champ.
    """

    user_id: str = ""
    username: str = ""
    role: str = ""
    request_id: str = ""

    @property
    def is_anonyme(self) -> bool:
        """True si aucune identité n'a été propagée par l'appelant."""
        return not self.user_id


_IDENTITE_VIDE = CallerIdentity()

# Portée à l'appel gRPC courant — posée par `IdentityInterceptor.intercept_service`,
# lue par `get_caller()` (typiquement au moment d'écrire une entrée `AuditLog`,
# dans la même transaction que le changement métier).
caller_identity: ContextVar[CallerIdentity] = ContextVar("caller_identity", default=_IDENTITE_VIDE)

_METADATA_USER_ID = "x-user-id"
_METADATA_USER_NAME = "x-user-name"
_METADATA_USER_ROLE = "x-user-role"
_METADATA_REQUEST_ID = "x-request-id"


def get_caller() -> CallerIdentity:
    """Renvoie l'identité de l'appelant de l'appel gRPC en cours.

    Renvoie une identité vide (`CallerIdentity()`) si aucune métadonnée
    d'identité n'a été propagée — jamais `None` : voir `CallerIdentity.is_anonyme`.
    """
    return caller_identity.get()


def _decoder_metadonnee(valeur: str | bytes) -> str:
    """Les métadonnées gRPC peuvent être `bytes` pour une clé `-bin` ; aucune
    des clés lues ici n'en est une, mais le type de la lib reste une union."""
    return valeur.decode() if isinstance(valeur, bytes) else valeur


def _identite_depuis_metadonnees(metadonnees: "Iterable[tuple[str, str | bytes]]") -> CallerIdentity:
    """Construit une `CallerIdentity` à partir des métadonnées d'invocation gRPC."""
    valeurs: dict[str, str] = {}
    for cle, valeur in metadonnees:
        if cle in (_METADATA_USER_ID, _METADATA_USER_NAME, _METADATA_USER_ROLE, _METADATA_REQUEST_ID):
            valeurs[cle] = _decoder_metadonnee(valeur)
    return CallerIdentity(
        user_id=valeurs.get(_METADATA_USER_ID, ""),
        username=valeurs.get(_METADATA_USER_NAME, ""),
        role=valeurs.get(_METADATA_USER_ROLE, ""),
        request_id=valeurs.get(_METADATA_REQUEST_ID, ""),
    )


class IdentityInterceptor(grpc.ServerInterceptor):
    """Pose l'identité de l'appelant (propagée par la gateway) dans un
    `ContextVar` le temps de l'appel gRPC, lue par `get_caller()` — typiquement
    au moment d'écrire une entrée `AuditLog` dans la même transaction que le
    changement métier (voir AUDIT_SGFE.md §10.7).

    Monté à côté de `ErrorHandlingInterceptor` dans `grpc_server.py`. Sans
    métadonnée d'identité (appel anonyme, ou test sans métadonnées),
    `get_caller()` renvoie une identité vide plutôt que de lever ou de
    renvoyer `None`.
    """

    def intercept_service(
        self,
        continuation: Callable[[grpc.HandlerCallDetails], grpc.RpcMethodHandler[Any, Any] | None],
        handler_call_details: grpc.HandlerCallDetails,
    ) -> grpc.RpcMethodHandler[Any, Any] | None:
        handler = continuation(handler_call_details)
        if handler is None or not handler.unary_unary:
            return handler

        original_behavior = handler.unary_unary
        identite = _identite_depuis_metadonnees(handler_call_details.invocation_metadata or ())

        def wrapped_behavior(request: Any, context: grpc.ServicerContext) -> Any:
            jeton = caller_identity.set(identite)
            try:
                return original_behavior(request, context)
            finally:
                caller_identity.reset(jeton)

        return grpc.unary_unary_rpc_method_handler(
            wrapped_behavior,
            request_deserializer=handler.request_deserializer,
            response_serializer=handler.response_serializer,
        )
