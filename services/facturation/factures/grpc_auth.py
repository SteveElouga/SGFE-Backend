# ─────────────────────────────────────────────────────────────────────────
# Fichier synchronisé — NE PAS ÉDITER DIRECTEMENT.
#
# Source canonique : libs/sgfe_common/sgfe_common/grpc_auth.py
# Après modification de la source, relancer : ./scripts/sync-grpc-lib.sh
# Vérifier l'absence de dérive       : ./scripts/sync-grpc-lib.sh --check
# ─────────────────────────────────────────────────────────────────────────
"""Authentification et chiffrement de la couche gRPC interne.

Jusqu'ici, quiconque atteignait les ports 50051-50058 appelait n'importe quel
service sans identifiant — création d'abonné, annulation de facture, tout. Les
huit `grpc_interceptors.py` ne faisaient que du mapping d'erreurs ; aucune
métadonnée n'était envoyée, aucun jeton vérifié. Le modèle de sécurité était
un seul mur, et rien derrière.

Ce module pose un second mur, en décalquant le motif que le service WhatsApp
utilise déjà (ANO-005) plutôt que d'en inventer un second :

  — un secret partagé, lu dans l'environnement ;
  — **fail-closed au démarrage** : sans secret, le serveur refuse de démarrer ;
  — comparaison en temps constant, pour ne rien apprendre par la durée ;
  — côté appelant, un intercepteur posé sur le canal plutôt qu'un paramètre
    `metadata=` répété sur 125 appels.

Ce second mur authentifie l'APPELANT applicatif (« qui parle »). Il ne dit
rien sur le TRANSPORT : le canal restait en clair, adapté à une machine unique
où les ports ne sortent pas du réseau Docker.

Ce module pose maintenant un troisième mur, orthogonal aux deux premiers :
mTLS (`add_secure_port`/`secure_channel`, tout en bas de ce fichier) chiffre
le trafic et authentifie mutuellement les deux extrémités au niveau TLS — un
tiers qui écoute le réseau Docker, ou un conteneur compromis sans le bon
certificat, ne peut ni lire le trafic ni initier de connexion, même s'il
devinait `INTERNAL_GRPC_KEY`. Les deux couches sont indépendantes et
cumulatives : retirer l'une ne désactive pas l'autre.

**Repli en clair, désormais toujours averti.** `GRPC_TLS_CA`/`GRPC_TLS_CERT`/
`GRPC_TLS_KEY` absentes ou illisibles → le serveur retombe sur
`add_insecure_port` et le client sur `insecure_channel`, comme avant — mais
ce repli journalise maintenant systématiquement un avertissement explicite
(`logger.warning`), là où il ne disait auparavant rien du tout. C'est le cas
des suites de tests, qui instancient un serveur ou un canal en mémoire sans
docker-compose ni certificats montés : elles n'ont pas à générer de PKI pour
rester vertes, et le nouvel avertissement ne les fait pas échouer.
Voir `scripts/generate-grpc-certs.sh` pour la génération de la CA/certificat.

**`GRPC_TLS_REQUIRED` (défaut `false`) — refus de démarrer en production.**
Positionnée à `true`, cette variable transforme le repli en clair ci-dessus
en refus explicite de démarrer (`CertificatTlsManquant`) dès qu'un des trois
fichiers de certificat est absent ou illisible : c'est la remédiation à
l'écart ASVS V12 relevé par `docs/CONFORMITE_SOC2_OWASP.md` (repli silencieux
sans garde-fou). Laissée à `false` par défaut pour ne rien changer au
comportement existant en développement/tests ; à positionner à `true` dans
l'environnement de chaque service en production.

**Ce module est partagé** entre les neuf composants gRPC (huit services +
la gateway) via `scripts/sync-grpc-lib.sh`, qui le recopie tel quel (avec un
bandeau d'en-tête indiquant la source) vers chaque emplacement de service —
voir `libs/sgfe_common/README.md` pour le choix d'architecture. Si ce fichier
porte le bandeau d'en-tête, c'est une copie : éditez la source canonique
(`libs/sgfe_common/sgfe_common/grpc_auth.py`) puis relancez le script, jamais
une copie directement. Volontairement copié plutôt qu'importé : chaque
service reste un module Django autonome (`<app>.grpc_auth`, y compris pour le
nom du logger `__name__` qu'inspectent les tests), et aucun `Dockerfile` n'a
besoin de voir en dehors de son propre dossier de service au moment du build.
"""

from __future__ import annotations

import hmac
import logging
import os
from collections import namedtuple
from pathlib import Path
from typing import Any, Callable

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


# ── Côté serveur — authentification applicative ─────────────────────────────


class AuthServerInterceptor(grpc.ServerInterceptor):
    """Refuse tout appel dont la métadonnée ne porte pas la clé attendue.

    Monté **avant** `ErrorHandlingInterceptor` : un appel non authentifié doit
    être rejeté avant d'atteindre la moindre logique métier, et son refus n'a
    pas à passer par le mapping d'exceptions.
    """

    def __init__(self, cle_attendue: str) -> None:
        self._cle = exiger_cle(cle_attendue, self.__class__.__name__).encode()
        self._refus: grpc.RpcMethodHandler[Any, Any] = grpc.unary_unary_rpc_method_handler(
            lambda requete, contexte: contexte.abort(
                grpc.StatusCode.UNAUTHENTICATED,
                "Appel interne non authentifié.",
            )
        )

    def intercept_service(
        self,
        continuation: Callable[[grpc.HandlerCallDetails], grpc.RpcMethodHandler[Any, Any] | None],
        handler_call_details: grpc.HandlerCallDetails,
    ) -> grpc.RpcMethodHandler[Any, Any] | None:
        methode = getattr(handler_call_details, "method", "") or ""
        if methode in METHODES_PUBLIQUES:
            return continuation(handler_call_details)

        fournie = ""
        for cle, valeur in handler_call_details.invocation_metadata or ():
            if cle == METADATA_KEY:
                # La métadonnée peut être `bytes` pour une clé "-bin" (grpc) ;
                # METADATA_KEY n'en est pas une, mais le type de la lib reste
                # une union — normalisé en str pour la comparaison ci-dessous.
                fournie = valeur.decode() if isinstance(valeur, bytes) else valeur
                break

        if not hmac.compare_digest(fournie.encode(), self._cle):
            # Journalisé sans la valeur reçue : un secret erroné reste un
            # secret, et l'écrire dans les logs en ferait une fuite.
            logger.warning("Appel gRPC refusé — clé interne absente ou invalide : %s", methode)
            return self._refus

        return continuation(handler_call_details)


# ── Côté client — authentification applicative ──────────────────────────────


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

    def intercept_unary_unary(
        self,
        continuation: Callable[[grpc.ClientCallDetails, Any], Any],
        client_call_details: grpc.ClientCallDetails,
        request: Any,
    ) -> Any:
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


# ── Chiffrement mutuel du transport (mTLS) ──────────────────────────────────
#
# Un seul certificat (généré par scripts/generate-grpc-certs.sh, SAN couvrant
# les neuf noms d'hôtes internes du docker-compose) sert à la fois d'identité
# SERVEUR (`identifiants_serveur_tls`/`ouvrir_port_grpc`) et d'identité
# CLIENTE (`identifiants_client_tls`/`canal_authentifie`) : les neuf
# composants sont des pairs égaux d'un même maillage interne, et aucun n'a
# besoin d'être distingué des huit autres à ce niveau — c'est déjà le rôle de
# `INTERNAL_GRPC_KEY` ci-dessus, qui authentifie l'appelant applicatif. Voir
# l'en-tête de `generate-grpc-certs.sh` si ce compromis doit un jour changer
# (cloisonnement plus fin, révocation par service).


class CertificatTlsManquant(RuntimeError):
    """Levée au démarrage quand `GRPC_TLS_REQUIRED=true` mais qu'un fichier de
    certificat mTLS (`GRPC_TLS_CA`/`GRPC_TLS_CERT`/`GRPC_TLS_KEY`) est absent
    ou illisible.

    Avant cette exception, l'absence de certificats produisait un repli
    silencieux sur `add_insecure_port`/`insecure_channel` — l'écart ASVS V12
    relevé par `docs/CONFORMITE_SOC2_OWASP.md` §3.3. `GRPC_TLS_REQUIRED=true`
    (à positionner en production) transforme ce repli en refus explicite de
    démarrer.
    """


def _tls_requis() -> bool:
    """Lit `GRPC_TLS_REQUIRED` : True si le mTLS interne est obligatoire.

    Défaut `False` — comportement historique inchangé : absence de
    certificats ⇒ repli en clair (désormais averti, voir `_materiel_tls`).
    Accepte les formes usuelles `1/true/yes/on` (insensible à la casse) ;
    toute autre valeur, y compris absente, vaut `False`.
    """
    return os.environ.get("GRPC_TLS_REQUIRED", "").strip().lower() in ("1", "true", "yes", "on")


def _lire_credentiel_tls(variable: str) -> bytes | None:
    """Lit le fichier pointé par `variable`, ou None si absent/illisible.

    Les trois variables (`GRPC_TLS_CA`, `GRPC_TLS_CERT`, `GRPC_TLS_KEY`) sont
    posées ensemble par docker-compose. En leur absence — tests unitaires,
    serveur ou canal instancié en mémoire — mTLS se désactive proprement (voir
    docstring du module) ; l'authentification applicative ci-dessus reste
    elle intacte.

    Si `GRPC_TLS_REQUIRED=true` et que le fichier pointé par une variable
    *définie* est illisible, refuse immédiatement de démarrer plutôt que de
    retomber sur un canal en clair (voir `CertificatTlsManquant`).
    """
    valeur = os.environ.get(variable)
    if not valeur:
        return None
    try:
        return Path(valeur).read_bytes()
    except OSError as exc:
        if _tls_requis():
            raise CertificatTlsManquant(
                f"{variable} défini ({valeur}) mais illisible ({exc}) — GRPC_TLS_REQUIRED=true "
                "exige un mTLS interne fonctionnel : le service refuse de démarrer plutôt que de "
                "retomber sur un canal gRPC en clair. Corrigez le montage du fichier de certificat "
                "ou désactivez GRPC_TLS_REQUIRED (développement uniquement)."
            ) from exc
        logger.warning(
            "%s défini (%s) mais illisible (%s) — repli sur gRPC en clair (GRPC_TLS_REQUIRED non activé).",
            variable,
            valeur,
            exc,
        )
        return None


def _materiel_tls() -> tuple[bytes, bytes, bytes] | None:
    """CA + certificat + clé, ou None si l'une des trois variables manque.

    Si `GRPC_TLS_REQUIRED=true`, l'absence d'un seul des trois fichiers lève
    `CertificatTlsManquant` (refus explicite de démarrer) au lieu de renvoyer
    `None` ; sinon, renvoie `None` après avoir journalisé un avertissement
    explicite — c'était la moitié manquante du dispositif : un repli qui
    n'avertissait jamais personne.
    """
    ca = _lire_credentiel_tls("GRPC_TLS_CA")
    cert = _lire_credentiel_tls("GRPC_TLS_CERT")
    cle = _lire_credentiel_tls("GRPC_TLS_KEY")
    if ca is None or cert is None or cle is None:
        if _tls_requis():
            manquantes = [
                nom
                for nom, valeur in (("GRPC_TLS_CA", ca), ("GRPC_TLS_CERT", cert), ("GRPC_TLS_KEY", cle))
                if valeur is None
            ]
            raise CertificatTlsManquant(
                "GRPC_TLS_REQUIRED=true exige un mTLS interne fonctionnel, mais la ou les "
                f"variable(s) suivante(s) ne pointent vers aucun fichier lisible : "
                f"{', '.join(manquantes)}. Générez les certificats "
                "(scripts/generate-grpc-certs.sh), montez-les et positionnez ces variables, ou "
                "désactivez GRPC_TLS_REQUIRED (développement uniquement)."
            )
        logger.warning(
            "mTLS gRPC non configuré (GRPC_TLS_CA/GRPC_TLS_CERT/GRPC_TLS_KEY absent(s) ou "
            "illisible(s)) — repli sur un canal gRPC en clair. Positionnez GRPC_TLS_REQUIRED=true "
            "en production pour transformer ce repli en refus de démarrage explicite."
        )
        return None
    return ca, cert, cle


def identifiants_serveur_tls() -> grpc.ServerCredentials | None:
    """Credentials mTLS serveur, ou None pour rester en clair (voir module).

    `require_client_auth=True` : le serveur exige lui aussi un certificat
    client valide, signé par la même CA — c'est ce qui rend l'authentification
    TLS mutuelle plutôt qu'à sens unique.
    """
    materiel = _materiel_tls()
    if materiel is None:
        return None
    ca, cert, cle = materiel
    return grpc.ssl_server_credentials(
        [(cle, cert)],
        root_certificates=ca,
        require_client_auth=True,
    )


def ouvrir_port_grpc(server: grpc.Server, port: int) -> None:
    """Ouvre `port` en mTLS si `GRPC_TLS_*` est configuré, sinon en clair.

    Point d'entrée unique appelé par `grpc_server.py::serve()` — évite de
    recopier la logique de repli en plus de la fabrique de credentials.
    """
    credentials = identifiants_serveur_tls()
    if credentials is not None:
        server.add_secure_port(f"[::]:{port}", credentials)
    else:
        server.add_insecure_port(f"[::]:{port}")


def identifiants_client_tls() -> grpc.ChannelCredentials | None:
    """Credentials mTLS client, ou None pour rester en clair (voir module)."""
    materiel = _materiel_tls()
    if materiel is None:
        return None
    ca, cert, cle = materiel
    return grpc.ssl_channel_credentials(
        root_certificates=ca,
        private_key=cle,
        certificate_chain=cert,
    )


def canal_authentifie(adresse: str, cle: str) -> grpc.Channel:
    """Ouvre un canal vers `adresse` qui authentifie tous ses appels.

    Remplace `grpc.insecure_channel(adresse)` sur les cinq fichiers de
    clients. Le transport est chiffré et authentifié mutuellement dès que
    `GRPC_TLS_*` est configuré (voir `identifiants_client_tls`) ; dans tous
    les cas, l'authentification applicative (`AuthClientInterceptor`)
    s'applique par-dessus — les deux couches sont indépendantes.
    """
    credentials = identifiants_client_tls()
    canal = grpc.secure_channel(adresse, credentials) if credentials is not None else grpc.insecure_channel(adresse)
    return grpc.intercept_channel(canal, AuthClientInterceptor(cle))
