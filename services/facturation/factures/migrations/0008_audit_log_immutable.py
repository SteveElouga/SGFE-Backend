"""Défense en profondeur — révoque UPDATE/DELETE sur `audit_log` pour le rôle
applicatif Postgres (voir AUDIT_SGFE.md §10.7, « Immuabilité »).

L'immuabilité de premier niveau est déjà applicative : `factures.audit.
enregistrer_audit` ne fait qu'un `AuditLog.objects.create(...)`, jamais
d'UPDATE ni de DELETE. Cette migration ajoute un second verrou, au niveau
base, qui tiendrait même si un bug (ou un accès direct à la base) tentait de
modifier ou supprimer une ligne du journal.

Rôle visé : celui de la connexion Postgres courante
(`schema_editor.connection.settings_dict["USER"]`, alimenté par la variable
d'environnement `FACTURATION_DB_USER`, défaut `facturation_user` — voir
`facturation/settings.py`) — pas une chaîne codée en dur, pour rester correct
si ce rôle est un jour renommé via l'environnement.

Note d'honnêteté — MISE À JOUR (voir `0010_audit_log_role_runtime`, qui
corrige ce qui suit). Ce commentaire documentait à l'origine une limite :
cette révocation n'est un verrou réel que si le rôle applicatif N'EST PAS le
propriétaire de la table, or dans la configuration d'alors (`docker-
compose.yml`), chaque service se connectait avec le MÊME rôle Postgres que
celui qui a créé la table via `migrate` (`POSTGRES_USER`) — il en était donc
le propriétaire, et cette révocation restait symbolique pour ce rôle précis.

**L'investigation menée pour `0010` a montré que c'était en réalité plus
grave que la seule propriété** : le rôle `POSTGRES_USER` de l'image Postgres
officielle est un SUPERUTILISATEUR (attribut hérité du bootstrap `initdb`),
qui contourne TOUT contrôle d'accès — REVOKE, ACL, propriété — pas seulement
la propriété d'une table. Vérifié empiriquement sur un conteneur Postgres
jetable : même un `ALTER TABLE ... OWNER TO <tiers>` combiné à un
`REVOKE ALL` ne bloque pas un rôle superutilisateur. Ce REVOKE-ci, à lui
seul, restait donc un verrou purement documentaire.

`0010_audit_log_role_runtime` (voir `factures/db_hardening.py` pour le
mécanisme complet) referme ce point : un second rôle Postgres `NOLOGIN`,
non superutilisateur, reçoit `SELECT, INSERT` sur `audit_log` (jamais
`UPDATE`/`DELETE`), et `grpc_server` bascule dessus via `SET ROLE` à chaque
connexion (`migrate` continue de tourner sous le rôle propriétaire, seul
habilité à faire du DDL). Cette migration-ci (`0008`) reste en place (le
REVOKE sur le rôle propriétaire ne fait de mal à personne et documente
l'intention), mais ce n'est plus elle qui protège `audit_log` en pratique —
c'est `0010`. Voir AUDIT_SGFE.md §8·J pour l'état à jour.

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
    """Réversible (`migrate factures 0007`) : rend UPDATE/DELETE au rôle applicatif."""
    role = _role_courant(schema_editor)
    if role is None:
        return
    with schema_editor.connection.cursor() as cursor:
        cursor.execute(f'GRANT UPDATE, DELETE ON {_TABLE} TO "{role}";')


class Migration(migrations.Migration):
    dependencies: list[tuple[str, str]] = [
        ("factures", "0007_auditlog"),
    ]

    operations: list[Any] = [
        migrations.RunPython(_revoke_update_delete, reverse_code=_grant_update_delete),
    ]
