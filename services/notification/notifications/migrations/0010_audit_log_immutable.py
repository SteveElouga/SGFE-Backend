"""Défense en profondeur — révoque UPDATE/DELETE sur `audit_log` pour le rôle
applicatif Postgres (voir AUDIT_SGFE.md §10.7, « Immuabilité »).

L'immuabilité de premier niveau est déjà applicative : `notifications.audit.
enregistrer_audit` ne fait qu'un `AuditLog.objects.create(...)`, jamais
d'UPDATE ni de DELETE. Cette migration ajoute un second verrou, au niveau
base, qui tiendrait même si un bug (ou un accès direct à la base) tentait de
modifier ou supprimer une ligne du journal.

Rôle visé : celui de la connexion Postgres courante
(`schema_editor.connection.settings_dict["USER"]`, alimenté par la variable
d'environnement `NOTIFICATION_DB_USER`, défaut `notification_user` — voir
`notification/settings.py`) — pas une chaîne codée en dur, pour rester
correct si ce rôle est un jour renommé via l'environnement.

Contrairement aux 6 services couverts dès l'origine par la conception
§10.7 (paiement, facturation puis campagne, config, auth, abonné), le
Notification Service n'a PAS suivi le chemin historique en deux temps
(REVOKE symbolique d'abord, rôle `_runtime` non superutilisateur ensuite) —
l'investigation qui a produit `libs/sgfe_common/sgfe_common/db_hardening.py`
existait déjà quand cette table a été créée ici. Cette migration-ci reste
néanmoins posée (le REVOKE sur le rôle propriétaire ne fait de mal à
personne et documente l'intention, même mécanique que les 6 autres
services), mais c'est `0011_audit_log_role_runtime` qui protège réellement
`audit_log` en pratique — voir AUDIT_SGFE.md §8·J pour le détail complet de
la raison (le rôle `POSTGRES_USER` de l'image Postgres officielle est un
SUPERUTILISATEUR, qui contourne tout REVOKE tant qu'aucun `SET ROLE` ne
bascule la session sur un rôle non superutilisateur).

Sans effet hors PostgreSQL : `REVOKE`/`GRANT` sur les rôles n'existent pas en
SQLite (moteur des tests locaux par défaut, voir `TESTING` dans
`settings.py`) — cette migration y est un no-op plutôt qu'une erreur, pour
ne pas casser `python manage.py test` en local.
"""

from __future__ import annotations

from typing import Any

from django.db import migrations
from django.db.backends.base.schema import BaseDatabaseSchemaEditor
from django.db.migrations.state import ProjectState

_TABLE = "audit_log"


def _role_courant(schema_editor: BaseDatabaseSchemaEditor) -> str | None:
    """Nom du rôle Postgres de la connexion courante, ou None (SQLite, ou
    rôle non nommé — ex. authentification "peer" locale sans utilisateur
    explicite, rien à révoquer dans ce cas)."""
    if schema_editor.connection.vendor != "postgresql":
        return None
    role = schema_editor.connection.settings_dict.get("USER")
    return role or None


def _revoke_update_delete(apps: ProjectState, schema_editor: BaseDatabaseSchemaEditor) -> None:
    """Révoque UPDATE/DELETE sur `audit_log` pour le rôle applicatif courant."""
    role = _role_courant(schema_editor)
    if role is None:
        return
    with schema_editor.connection.cursor() as cursor:
        cursor.execute(f'REVOKE UPDATE, DELETE ON {_TABLE} FROM "{role}";')


def _grant_update_delete(apps: ProjectState, schema_editor: BaseDatabaseSchemaEditor) -> None:
    """Réversible (`migrate notifications 0009`) : rend UPDATE/DELETE au rôle applicatif."""
    role = _role_courant(schema_editor)
    if role is None:
        return
    with schema_editor.connection.cursor() as cursor:
        cursor.execute(f'GRANT UPDATE, DELETE ON {_TABLE} TO "{role}";')


class Migration(migrations.Migration):
    dependencies: list[tuple[str, str]] = [
        ("notifications", "0009_auditlog"),
    ]

    operations: list[Any] = [
        migrations.RunPython(_revoke_update_delete, reverse_code=_grant_update_delete),
    ]
