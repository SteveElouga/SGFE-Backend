"""Écriture du journal d'audit (`AuditLog`) — voir AUDIT_SGFE.md §10.7.

Ce module ne fait qu'écrire ; jamais de lecture, de mise à jour ni de
suppression (immuabilité applicative — renforcée niveau base par la migration
`0010_audit_log_immutable`, qui révoque UPDATE/DELETE sur `audit_log` pour le
rôle applicatif Postgres).
"""

from __future__ import annotations

from abonnes.models import AuditLog


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
    """
    # Import différé (et non en tête de module) : `grpc_interceptors` importe
    # `ValidationError` depuis `abonnes.services`, qui importe ce module pour
    # `enregistrer_audit` — un import en tête de fichier créerait un cycle
    # d'import (services → audit → grpc_interceptors → services) qui échoue
    # dès que `grpc_interceptors` est le premier des trois chargé (cas réel de
    # `grpc_server.py`). Ce cycle n'existe pas côté Paiement Service (sa
    # `ValidationError` vient directement de Django, pas de son `services.py`).
    from abonnes.grpc_interceptors import get_caller  # noqa: PLC0415

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
