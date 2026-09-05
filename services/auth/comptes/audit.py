"""Écriture du journal d'audit (`AuditLog`) — voir AUDIT_SGFE.md §10.7.

Ce module ne fait qu'écrire ; jamais de lecture, de mise à jour ni de
suppression (immuabilité applicative — renforcée niveau base par la migration
`0007_audit_log_immutable`, qui révoque UPDATE/DELETE sur `audit_log` pour le
rôle applicatif Postgres).
"""

from __future__ import annotations

from django.db import transaction

from .models import AuditLog


def enregistrer_audit(action: str, objet_type: str, objet_id: str, detail: str = "") -> None:
    """Écrit une entrée d'audit pour la mutation métier en cours.

    À appeler DANS LA MÊME transaction Django (`transaction.atomic()`) que le
    changement métier qu'elle documente — jamais un appel séparé après coup :
    c'est cette même transaction ambiante qui garantit que l'écriture d'audit
    et le changement métier commitent, ou échouent, ensemble.

    L'acteur est lu depuis `get_caller()` (identité propagée par la gateway
    via les métadonnées gRPC posées par `IdentityClientInterceptor`, voir
    `grpc_interceptors.py`) — une identité vide (appel sans identité propagée,
    ex. tâche de fond) journalise un acteur vide plutôt que de lever : l'audit
    ne doit jamais faire échouer la mutation qu'il documente.

    Import différé de `get_caller` : contrairement à Paiement/Facturation,
    `comptes.grpc_interceptors` importe `comptes.services` (pour le mapping
    des exceptions métier vers les codes gRPC, `ErrorHandlingInterceptor`) —
    or `comptes.services` importe ce module. Un import en tête de fichier
    créerait un cycle (services -> audit -> grpc_interceptors -> services) ;
    différé, il est résolu au premier appel, une fois tous les modules chargés.
    """
    from .grpc_interceptors import get_caller

    caller = get_caller()
    AuditLog.objects.create(
        action=action,
        objet_type=objet_type,
        objet_id=objet_id,
        acteur_id=caller.user_id,
        acteur_nom=caller.username,
        acteur_role=caller.role,
        detail=detail,
    )


def enregistrer_evenement_securite(
    type_evenement: str,
    detail: str = "",
    acteur_id: str = "",
    acteur_nom: str = "",
    acteur_role: str = "",
    request_id: str = "",
) -> None:
    """Centralise un événement de sécurité poussé par un composant tiers (la
    gateway aujourd'hui — refus de rôle, échec de validation de jeton) dans
    l'`AuditLog` de ce service. Voir AUDIT_SGFE.md §J, "Journalisation de
    sécurité centralisée et inviolable" : la conception §10.7 prévoyait de
    « logger via AuditLog si un service concerné est impliqué, sinon un
    logger dédié » — Auth est ce service pour TOUS les événements de
    sécurité de la gateway (propriétaire naturel de l'identité).

    Contrairement à `enregistrer_audit`, cet événement n'accompagne AUCUNE
    mutation métier de ce service : il n'y a donc rien d'autre à committer
    avec lui, et cette fonction ouvre sa PROPRE transaction dédiée plutôt que
    de supposer une transaction ambiante ouverte par un appelant.

    L'acteur est fourni EXPLICITEMENT par l'appelant (pas relu depuis
    `get_caller()`/l'identité propagée par les métadonnées gRPC) : l'appelant
    (la gateway) documente ici un événement qui a eu lieu CÔTÉ GATEWAY,
    parfois avant même qu'une identité complète soit résolue (un jeton
    invalide n'a par définition aucune identité à propager). S'appuyer sur
    l'identité propagée par gRPC serait en outre redondant avec les limites
    déjà documentées de ce mécanisme (souscriptions GraphQL, appels
    anonymes — voir `identity_context.py` côté gateway).

    Lève `ValueError` si `type_evenement` est vide — un événement de sécurité
    sans type n'est pas exploitable par un futur auditeur.
    """
    if not type_evenement:
        raise ValueError("type_evenement est obligatoire")
    with transaction.atomic():
        AuditLog.objects.create(
            action=type_evenement,
            objet_type="EvenementSecuriteGateway",
            objet_id=request_id,
            acteur_id=acteur_id,
            acteur_nom=acteur_nom,
            acteur_role=acteur_role,
            detail=detail,
        )
