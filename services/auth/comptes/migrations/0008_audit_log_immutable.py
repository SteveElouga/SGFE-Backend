"""Défense en profondeur — révoque UPDATE/DELETE sur `audit_log` pour le rôle
applicatif Postgres (voir AUDIT_SGFE.md §10.7, « Immuabilité »).

L'immuabilité de premier niveau est déjà applicative : `comptes.audit.
enregistrer_audit` ne fait qu'un `AuditLog.objects.create(...)`, jamais
d'UPDATE ni de DELETE. Cette migration ajoute un second verrou, au niveau
base, qui tiendrait même si un bug (ou un accès direct à la base) tentait de
modifier ou supprimer une ligne du journal.

Rôle visé : celui de la connexion Postgres courante
(`schema_editor.connection.settings_dict["USER"]`, alimenté par la variable
d'environnement `AUTH_DB_USER`, défaut `auth_user` — voir
`auth/settings.py`) — pas une chaîne codée en dur, pour rester correct
si ce rôle est un jour renommé via l'environnement.

Note d'honnêteté (limite connue de PostgreSQL). Cette révocation n'est un
verrou réel que si le rôle applicatif N'EST PAS le propriétaire de la table :
un propriétaire conserve ses privilèges DML même après un REVOKE explicite
sur lui-même (la propriété prime sur la liste de contrôle d'accès). Dans la
configuration actuelle du dépôt (`docker-compose.yml`), chaque service se
connecte avec le MÊME rôle Postgres que celui qui a créé la table via
`migrate` (`POSTGRES_USER`) : il en est donc le propriétaire, et cette
révocation reste une défense en profondeur symbolique pour ce rôle précis
(elle bloquerait un rôle tiers auquel on accorderait un accès restreint,
mais pas le rôle applicatif actuel lui-même, qui reste propriétaire). Un
durcissement réel exigerait un rôle Postgres dédié aux migrations
(propriétaire) distinct du rôle d'exécution de l'application (non
propriétaire, avec GRANT ciblé sur INSERT/SELECT) — changement d'architecture
plus large, explicitement hors périmètre de cette PR (voir AUDIT_SGFE.md §J,
« reste à faire »).

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
    """Réversible (`migrate comptes 0007`) : rend UPDATE/DELETE au rôle applicatif."""
    role = _role_courant(schema_editor)
    if role is None:
        return
    with schema_editor.connection.cursor() as cursor:
        cursor.execute(f'GRANT UPDATE, DELETE ON {_TABLE} TO "{role}";')


class Migration(migrations.Migration):
    dependencies: list[tuple[str, str]] = [
        ("comptes", "0007_auditlog"),
    ]

    operations: list[Any] = [
        migrations.RunPython(_revoke_update_delete, reverse_code=_grant_update_delete),
    ]
