"""Écriture du journal d'audit (`AuditLog`) — voir AUDIT_SGFE.md §10.7.

Contrairement aux 6 autres services couverts par la conception §10.7, ce
module n'est appelé que par les 3 RPC identifiées comme des mutations
sensibles à part entière (`CreerDiffusion`, `RevoquerToken`,
`RevoquerTousTokens` — voir `notifications/models.py::AuditLog` pour le
détail de cette exception assumée).

Ce module ne fait qu'écrire ; jamais de lecture, de mise à jour ni de
suppression (immuabilité applicative — renforcée niveau base par la migration
`0010_audit_log_immutable`, qui révoque UPDATE/DELETE sur `audit_log` pour le
rôle applicatif Postgres).
"""

from __future__ import annotations

from .grpc_interceptors import get_caller
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

    `detail` ne doit jamais porter de PII (numéro de téléphone, contenu d'un
    message) — uniquement des métadonnées (compteurs, identifiants
    techniques), cohérent avec le reste du projet.
    """
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
