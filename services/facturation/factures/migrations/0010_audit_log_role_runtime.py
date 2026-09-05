"""Isolation Postgres réelle de `audit_log` — complète `0008_audit_log_immutable`.

Voir AUDIT_SGFE.md §8·J et le module `factures/db_hardening.py` (copie
synchronisée depuis `libs/sgfe_common/`, source canonique et justification
complète — commentaire de tête du fichier) pour le détail.

En résumé : `0008_audit_log_immutable` documentait honnêtement que son
`REVOKE UPDATE, DELETE` restait un verrou symbolique parce que le rôle
applicatif est *propriétaire* de la table. L'investigation menée pour cette
migration a montré que c'est en réalité plus grave que la seule propriété :
le rôle défini par `FACTURATION_DB_USER`/`POSTGRES_USER` est un
**superutilisateur** Postgres (attribut hérité de l'`initdb` bootstrap de
l'image officielle `postgres`), qui contourne TOUT contrôle d'accès, pas
seulement la propriété d'une table — vérifié empiriquement sur un conteneur
Postgres jetable (`postgres:16-alpine`).

Cette migration ne touche pas au rôle de connexion utilisé par `migrate`
(toujours `FACTURATION_DB_USER`, toujours propriétaire/superutilisateur —
aucun changement à `docker-compose.yml` ni à `settings.py::DATABASES`). Elle
crée un second rôle Postgres, `NOLOGIN`, `<FACTURATION_DB_USER>_runtime` :
- accorde-lui tous les droits sur toutes les tables du service (existantes
  ET futures, via `ALTER DEFAULT PRIVILEGES`) ;
- restreint spécifiquement `audit_log` à `SELECT, INSERT`.

`factures/db_hardening.py` (câblé dans `facturation/settings.py`, juste
après `DATABASES`) bascule ensuite CHAQUE connexion Postgres de
`grpc_server` (et du relais outbox `outbox_relay_job`, qui tourne dans le
même process) sur ce rôle via `SET ROLE`, dès l'établissement de la
connexion — sauf pour `migrate`, qui continue de s'exécuter sous le rôle
propriétaire. `enregistrer_audit` reste dans LA MÊME connexion/transaction
que la mutation métier qu'elle documente (`SET ROLE` change l'identité
effective de la session, pas la connexion réseau) — aucune régression sur
l'atomicité documentée dans `factures/audit.py`.

Vérifié réellement (pas seulement en théorie) :
- `factures/tests/test_migration_audit_log_role_runtime.py` — couvre la
  génération SQL (schema_editor simulé, comme `0008`) ;
- `factures/tests/test_db_hardening_postgres.py` — test d'intégration
  Postgres réel (gaté par `FORCE_POSTGRES_TESTS`, comme le job CI
  `test-facturation` qui tourne déjà contre un `postgres:16-alpine`
  jetable) : prouve qu'avec `<rôle>_runtime` actif, `UPDATE`/`DELETE` sur
  `audit_log` échouent bien (`permission denied`), là où le rôle
  propriétaire d'origine réussissait toujours malgré `0008`.
"""

from __future__ import annotations

from typing import Any

from django.db import migrations
from django.db.backends.base.schema import BaseDatabaseSchemaEditor
from django.db.migrations.state import ProjectState

from factures.db_hardening import creer_role_runtime_et_isoler_table, supprimer_role_runtime

_TABLE = "audit_log"


def _isoler_audit_log(apps: ProjectState, schema_editor: BaseDatabaseSchemaEditor) -> None:
    creer_role_runtime_et_isoler_table(schema_editor, tables_immuables=(_TABLE,))


def _desisoler_audit_log(apps: ProjectState, schema_editor: BaseDatabaseSchemaEditor) -> None:
    supprimer_role_runtime(schema_editor, tables_immuables=(_TABLE,))


class Migration(migrations.Migration):
    dependencies: list[tuple[str, str]] = [
        ("factures", "0009_outboxevent"),
    ]

    operations: list[Any] = [
        migrations.RunPython(_isoler_audit_log, reverse_code=_desisoler_audit_log),
    ]
