"""Authentification de la couche gRPC interne.

Jusqu'ici, quiconque atteignait les ports 50051-50058 appelait n'importe quel
service sans identifiant — création d'abonné, annulation de facture, tout. Les
huit `grpc_interceptors.py` ne faisaient que du mapping d'erreurs ; aucune
métadonnée n'était envoyée, aucun jeton vérifié. Le modèle de sécurité était
un seul mur, et rien derrière.

Ce module pose le second mur, en décalquant le motif que le service WhatsApp
utilise déjà (ANO-005) plutôt que d'en inventer un second :

  — un secret partagé, lu dans l'environnement ;
  — **fail-closed au démarrage** : sans secret, le serveur refuse de démarrer ;
  — comparaison en temps constant, pour ne rien apprendre par la durée ;
  — côté appelant, un intercepteur posé sur le canal plutôt qu'un paramètre
    `metadata=` répété sur 125 appels.

Ce n'est pas de la mTLS. C'est un secret partagé, adapté à une machine unique
où les ports ne sortent pas du réseau Docker — et qui ferme le trou béant
qu'était l'absence totale de contrôle. Le jour où les services se répartiront
sur plusieurs hôtes, la mTLS deviendra le bon outil ; ce module aura alors
posé la discipline d'appel, qui est le vrai coût de la migration.
"""

from __future__ import annotations

import hmac
import logging
from collections import namedtuple

import grpc

logger = logging.getLogger(__name__)

# Les clés de métadonnée gRPC sont normalisées en minuscules par le transport.
# L'écrire ainsi ici évite une comparaison qui échouerait silencieusement.
METADATA_KEY = "x-internal-key"

# Méthodes servies sans authentification. Vide aujourd'hui : aucune méthode
# interne n'a de raison d'être publique. Les sondes de santé passent par HTTP,
# pas par gRPC.
METHODES_PUBLIQUES: frozenset[str] = frozenset()


class CleInterneManquante(RuntimeError):
    """Levée au démarrage quand le secret n'est pas configuré."""


def exiger_cle(cle: str | None, composant: str) -> str:
    """Renvoie la clé, ou refuse de démarrer.

    Le fail-closed est délibéré, y compris en développement local. Une valeur
    par défaut silencieuse produirait exactement la situation qu'on corrige :
    un contrôle qui a l'air posé et qui ne protège rien.
    """
    if not cle:
        raise CleInterneManquante(
            f"{composant} : INTERNAL_GRPC_KEY absente ou vide. Le service refuse de "
            "démarrer sans clé d'authentification interne. Définissez-la dans "
            "l'environnement (y compris en local) avant de lancer le service."
        )
    return cle


# ── Côté serveur ─────────────────────────────────────────────────────────────


class AuthServerInterceptor(grpc.ServerInterceptor):
    """Refuse tout appel dont la métadonnée ne porte pas la clé attendue.

    Monté **avant** `ErrorHandlingInterceptor` : un appel non authentifié doit
    être rejeté avant d'atteindre la moindre logique métier, et son refus n'a
    pas à passer par le mapping d'exceptions.
    """

    def __init__(self, cle_attendue: str) -> None:
        self._cle = exiger_cle(cle_attendue, self.__class__.__name__).encode()
        self._refus = grpc.unary_unary_rpc_method_handler(
            lambda requete, contexte: contexte.abort(
                grpc.StatusCode.UNAUTHENTICATED,
                "Appel interne non authentifié.",
            )
        )

    def intercept_service(self, continuation, handler_call_details):
        methode = getattr(handler_call_details, "method", "") or ""
        if methode in METHODES_PUBLIQUES:
            return continuation(handler_call_details)

        fournie = ""
        for cle, valeur in handler_call_details.invocation_metadata or ():
            if cle == METADATA_KEY:
                fournie = valeur
                break

        if not hmac.compare_digest(fournie.encode(), self._cle):
            # Journalisé sans la valeur reçue : un secret erroné reste un
            # secret, et l'écrire dans les logs en ferait une fuite.
            logger.warning("Appel gRPC refusé — clé interne absente ou invalide : %s", methode)
            return self._refus

        return continuation(handler_call_details)


# ── Côté client ──────────────────────────────────────────────────────────────


class _DetailsAppel(
    namedtuple("_DetailsAppel", ("method", "timeout", "metadata", "credentials")),
    grpc.ClientCallDetails,
):
    """`ClientCallDetails` est une interface, pas une classe instanciable.

    grpc-python n'expose aucune fabrique publique : reconstruire le tuple est
    le motif documenté pour enrichir la métadonnée d'un appel sortant.
    """


class AuthClientInterceptor(grpc.UnaryUnaryClientInterceptor):
    """Ajoute la clé interne à chaque appel sortant du canal qu'il intercepte.

    Posé une fois à la création du canal, il couvre tous les appels qui y
    transitent — présents et futurs. C'est ce qui distingue cette approche du
    paramètre `metadata=` : on ne peut pas oublier d'authentifier un appel
    qu'on ajoutera demain.
    """

    def __init__(self, cle: str) -> None:
        self._cle = exiger_cle(cle, self.__class__.__name__)

    def intercept_unary_unary(self, continuation, client_call_details, request):
        metadata = list(client_call_details.metadata or ())
        metadata.append((METADATA_KEY, self._cle))
        return continuation(
            _DetailsAppel(
                client_call_details.method,
                client_call_details.timeout,
                metadata,
                client_call_details.credentials,
            ),
            request,
        )


def canal_authentifie(adresse: str, cle: str) -> grpc.Channel:
    """Ouvre un canal vers `adresse` qui authentifie tous ses appels.

    Remplace `grpc.insecure_channel(adresse)` sur les cinq fichiers de clients.
    Le canal reste en clair — le chiffrement du transport est un autre sujet,
    et il n'a pas de sens sur une boucle locale Docker.
    """
    return grpc.intercept_channel(
        grpc.insecure_channel(adresse),
        AuthClientInterceptor(cle),
    )
