# ─────────────────────────────────────────────────────────────────────────
# Fichier synchronisé — NE PAS ÉDITER DIRECTEMENT.
#
# Source canonique : libs/sgfe_common/sgfe_common/db_hardening.py
# Après modification de la source, relancer : ./scripts/sync-db-hardening-lib.sh
# Vérifier l'absence de dérive       : ./scripts/sync-db-hardening-lib.sh --check
# ─────────────────────────────────────────────────────────────────────────
"""Isolation Postgres du rôle applicatif d'exécution — voir AUDIT_SGFE.md §8·J.

## Constat empirique (à l'origine de ce module)

La migration `..._audit_log_immutable` de chaque service (introduite par la
PR #193, Paiement/Facturation) fait un `REVOKE UPDATE, DELETE ON audit_log
FROM <rôle applicatif>` pour renforcer, au niveau base, l'immuabilité déjà
garantie côté applicatif (`enregistrer_audit` ne fait qu'un `.create(...)`).

Cette révocation s'est révélée **sans aucun effet réel** — pas seulement
« limitée », comme le documentait honnêtement le commentaire original de
cette migration, mais totalement inopérante, pour une raison plus profonde
que la seule propriété de la table :

**Le rôle défini par `POSTGRES_USER` dans `docker-compose.yml` est un
SUPERUTILISATEUR PostgreSQL** — pas seulement le propriétaire de la table.
L'image officielle `postgres` bootstrap le cluster via `initdb
--username=$POSTGRES_USER` : ce rôle hérite de tous les attributs du rôle
`postgres` par défaut. Vérifié empiriquement (conteneur Postgres jetable,
`postgres:16-alpine`, `docker run --rm -e POSTGRES_USER=x ...`, puis `\\du`) :

    Nom du rôle | Attributs
    ------------+------------------------------------------------------------
    x           | Superuser, Create role, Create DB, Replication, Bypass RLS

Un superutilisateur **contourne systématiquement tous les contrôles
d'accès** — GRANT, REVOKE, propriété de table, RLS — pas seulement quand il
est propriétaire. Vérifié : même après `ALTER TABLE ... OWNER TO <un_tiers>`
ET `REVOKE ALL ...` sur ce rôle, un rôle superutilisateur exécute quand même
`UPDATE`/`DELETE` sans erreur. Aucune combinaison de `REVOKE`/`ALTER TABLE
OWNER TO` n'y change quoi que ce soit tant que le rôle de connexion reste
superutilisateur : la seule protection qui fonctionne est qu'aucune requête
applicative ne s'exécute jamais avec un tel rôle.

## Le mécanisme retenu : `SET ROLE`, pas une deuxième identité de connexion

Isoler le trafic applicatif derrière un second rôle *non superutilisateur*
sans casser l'atomicité de `enregistrer_audit` (qui DOIT s'exécuter dans la
même transaction Postgres que la mutation métier qu'elle documente — voir
`<app>/audit.py` de chaque service) exclut d'emblée une deuxième connexion
Django (un second alias `DATABASES`) : deux connexions = deux sessions
Postgres = deux transactions indépendantes, donc plus aucune garantie que
l'écriture d'audit et le changement métier commitent ensemble.

La solution retenue reste sur l'UNIQUE connexion Django existante (alias
`default`, mêmes `<SERVICE>_DB_USER`/`<SERVICE>_DB_PASSWORD` qu'aujourd'hui —
**aucun nouveau secret, aucune variable d'environnement supplémentaire,
aucun changement à `docker-compose.yml`**) et bascule cette connexion, une
fois établie, sur un second rôle via `SET ROLE` — une commande Postgres qui
change l'identité EFFECTIVE de la session sans changer sa connexion réseau
ni sa transaction : `enregistrer_audit` reste donc dans la même transaction
que le changement métier, exactement comme avant.

Vérifié empiriquement (même conteneur jetable) : après `SET ROLE
app_runtime` (rôle `NOLOGIN`, sans l'attribut `Superuser`), la session perd
RÉELLEMENT son statut superutilisateur (`rolsuper = false` pour
`current_user`), `UPDATE`/`DELETE` sur une table où `app_runtime` n'a que
`SELECT, INSERT` échouent avec `permission denied for table`, et même
`CREATE ROLE` échoue (« Only roles with the CREATEROLE attribute may create
roles ») — la preuve que ce n'est pas qu'un blocage au niveau de la table
mais une vraie perte de privilèges de session. `RESET ROLE` restaure
intégralement les pouvoirs du rôle de connexion d'origine.

## Ce que fait ce module

1. `creer_role_runtime_et_isoler_table` (à appeler depuis une migration
   `RunPython`, DONC toujours sous le rôle propriétaire/superutilisateur
   actuel — c'est lui qui a le droit `CREATEROLE` nécessaire) :
   - crée (idempotent) un rôle `NOLOGIN` `<rôle_courant>_runtime` ;
   - le rend membre du rôle courant (ceinture-bretelles : un superutilisateur
     peut de toute façon `SET ROLE` vers n'importe quel rôle sans cette
     adhésion, mais elle reste correcte si ce rôle propriétaire cessait un
     jour d'être superutilisateur) ;
   - lui accorde TOUS les droits sur TOUTES les tables/séquences existantes
     du schéma `public`, **y compris les tables créées par de futures
     migrations** via `ALTER DEFAULT PRIVILEGES` (les migrations futures
     continuent de tourner sous le rôle propriétaire — aucun changement là —
     donc chaque nouvelle table doit re-propager ses droits au rôle
     `_runtime` automatiquement, sans quoi une nouvelle migration casserait
     silencieusement l'application au prochain redémarrage) ;
   - restreint spécifiquement la ou les tables passées dans
     `tables_immuables` à `SELECT, INSERT` (jamais `UPDATE`/`DELETE`).
   Peut être appelée plusieurs fois dans la vie d'un service (une fois par
   nouvelle table à rendre immuable) — toutes ses opérations sont
   idempotentes.

2. `connecter_isolement_runtime` (appelée UNE FOIS depuis `settings.py`,
   juste après la définition de `DATABASES`) : relie
   `activer_isolement_runtime` au signal Django
   `django.db.backends.signals.connection_created`, pour que CHAQUE nouvelle
   connexion Postgres bascule automatiquement sur le rôle `_runtime` — SAUF
   les commandes qui ont besoin des pouvoirs du propriétaire (`migrate`,
   `makemigrations`, `sqlmigrate`, `test` — cette dernière parce que
   `manage.py test` fait tourner les migrations pour construire la base de
   test avant le moindre test).

## Comment un futur service adopte ce mécanisme

(Campagne, Abonné, Auth, Config — en cours d'ajout de leur propre `AuditLog`
sur d'autres branches au moment où ce module a été écrit.)

1. Ajouter la destination du service à `scripts/sync-db-hardening-lib.sh`
   (tableau `DESTINATIONS`), puis lancer `./scripts/sync-db-hardening-lib.sh`
   — copie ce fichier vers `services/<service>/<app>/db_hardening.py`.
2. Dans `settings.py` du service, juste après la définition de `DATABASES` :

   ```python
   from <app>.db_hardening import connecter_isolement_runtime

   connecter_isolement_runtime()
   ```

3. Dans une NOUVELLE migration (jamais une édition d'une migration déjà
   appliquée), postérieure à celle qui crée la table à protéger :

   ```python
   from django.db import migrations
   from <app>.db_hardening import creer_role_runtime_et_isoler_table, supprimer_role_runtime

   _TABLE = "audit_log"


   def _forward(apps, schema_editor):
       creer_role_runtime_et_isoler_table(schema_editor, tables_immuables=(_TABLE,))


   def _reverse(apps, schema_editor):
       supprimer_role_runtime(schema_editor, tables_immuables=(_TABLE,))


   class Migration(migrations.Migration):
       dependencies = [("<app>", "<précédente_migration>")]
       operations = [migrations.RunPython(_forward, reverse_code=_reverse)]
   ```

4. Lancer `./scripts/sync-db-hardening-lib.sh --check` (déjà en CI, job
   `check-db-hardening-lib-drift`) pour confirmer l'absence de dérive.

Aucun changement à `docker-compose.yml` n'est nécessaire : ce mécanisme
n'introduit ni nouveau rôle *authentifiable*, ni nouveau secret, ni nouvelle
variable d'environnement — `<rôle>_runtime` est `NOLOGIN`, jamais atteint
autrement que par `SET ROLE` depuis la connexion existante.

## Pourquoi pas un vrai package Python importé

Même choix, et pour les mêmes raisons, que `sgfe_common.grpc_auth` — voir
`libs/sgfe_common/README.md`. En bref : `docker-compose.yml` scope le
contexte de build de chaque service à son seul dossier (`build:
./services/X`), donc aucun `Dockerfile` ne peut voir `libs/sgfe_common/` au
moment du build. Ce fichier reste la source canonique, recopiée telle
quelle par `scripts/sync-db-hardening-lib.sh` (même mécanique de bandeau +
vérification de hash que `scripts/sync-grpc-lib.sh`).
"""

from __future__ import annotations

import logging
import sys
from typing import Iterable

from django.db.backends.base.base import BaseDatabaseWrapper
from django.db.backends.base.schema import BaseDatabaseSchemaEditor
from django.db.backends.signals import connection_created

logger = logging.getLogger(__name__)

# Commandes `manage.py` qui doivent garder les pleins pouvoirs du rôle
# propriétaire (DDL et/ou construction de la base de test) — toute autre
# commande (`grpc_server`, `runserver`, `shell`, commandes métier ponctuelles,
# etc.) bascule automatiquement sur le rôle `_runtime` restreint.
COMMANDES_ROLE_PROPRIETAIRE: frozenset[str] = frozenset({"migrate", "makemigrations", "sqlmigrate", "test"})


def role_proprietaire(connection: BaseDatabaseWrapper) -> str | None:
    """Rôle Postgres de la connexion `connection`, ou None (SQLite, ou rôle
    non nommé — authentification "peer" locale sans utilisateur explicite,
    rien à isoler dans ce cas)."""
    if connection.vendor != "postgresql":
        return None
    role = connection.settings_dict.get("USER")
    return role or None


def role_runtime(connection: BaseDatabaseWrapper) -> str | None:
    """Nom déterministe du rôle applicatif restreint : `<rôle_propriétaire>_runtime`.

    Dérivé du rôle de connexion courant plutôt que codé en dur, pour rester
    correct si ce rôle est un jour renommé via l'environnement — même choix
    que la migration `..._audit_log_immutable` d'origine pour `_role_courant`.
    """
    proprietaire = role_proprietaire(connection)
    if proprietaire is None:
        return None
    return f"{proprietaire}_runtime"


def creer_role_runtime_et_isoler_table(
    schema_editor: BaseDatabaseSchemaEditor,
    *,
    tables_immuables: Iterable[str],
) -> None:
    """RunPython (forward) : crée/configure le rôle `_runtime` et restreint
    `tables_immuables` à `SELECT, INSERT` pour ce rôle.

    À appeler depuis une migration postérieure à la création des tables
    listées. Idempotent — peut être rappelée (nouvelle table à protéger, ou
    replay de la migration) sans effet de bord.

    No-op hors PostgreSQL (SQLite des tests locaux, voir `TESTING` dans
    `settings.py`) : `REVOKE`/`GRANT`/rôles n'y existent pas.
    """
    proprietaire = role_proprietaire(schema_editor.connection)
    if proprietaire is None:
        return
    runtime = f"{proprietaire}_runtime"
    with schema_editor.connection.cursor() as cursor:
        cursor.execute("SELECT 1 FROM pg_roles WHERE rolname = %s", [runtime])
        if cursor.fetchone() is None:
            cursor.execute(f'CREATE ROLE "{runtime}" NOLOGIN;')
        cursor.execute(f'GRANT "{runtime}" TO "{proprietaire}";')
        cursor.execute(f'GRANT USAGE ON SCHEMA public TO "{runtime}";')
        cursor.execute(f'GRANT ALL PRIVILEGES ON ALL TABLES IN SCHEMA public TO "{runtime}";')
        cursor.execute(f'GRANT ALL PRIVILEGES ON ALL SEQUENCES IN SCHEMA public TO "{runtime}";')
        cursor.execute(
            f'ALTER DEFAULT PRIVILEGES FOR ROLE "{proprietaire}" IN SCHEMA public GRANT ALL ON TABLES TO "{runtime}";'
        )
        cursor.execute(
            f'ALTER DEFAULT PRIVILEGES FOR ROLE "{proprietaire}" IN SCHEMA public '
            f'GRANT ALL ON SEQUENCES TO "{runtime}";'
        )
        for table in tables_immuables:
            cursor.execute(f'REVOKE UPDATE, DELETE ON "{table}" FROM "{runtime}";')
    logger.info("Rôle Postgres restreint '%s' configuré (membre de '%s').", runtime, proprietaire)


def supprimer_role_runtime(
    schema_editor: BaseDatabaseSchemaEditor,
    *,
    tables_immuables: Iterable[str],
) -> None:
    """RunPython (reverse) : annule `creer_role_runtime_et_isoler_table` —
    retire les droits et l'adhésion, puis supprime le rôle `_runtime`.

    No-op hors PostgreSQL, ou si le rôle `_runtime` n'existe pas déjà
    (migration inverse rejouée deux fois, ou jamais appliquée).
    """
    proprietaire = role_proprietaire(schema_editor.connection)
    if proprietaire is None:
        return
    runtime = f"{proprietaire}_runtime"
    with schema_editor.connection.cursor() as cursor:
        cursor.execute("SELECT 1 FROM pg_roles WHERE rolname = %s", [runtime])
        if cursor.fetchone() is None:
            return
        for table in tables_immuables:
            cursor.execute(f'GRANT UPDATE, DELETE ON "{table}" TO "{runtime}";')
        cursor.execute(
            f'ALTER DEFAULT PRIVILEGES FOR ROLE "{proprietaire}" IN SCHEMA public '
            f'REVOKE ALL ON TABLES FROM "{runtime}";'
        )
        cursor.execute(
            f'ALTER DEFAULT PRIVILEGES FOR ROLE "{proprietaire}" IN SCHEMA public '
            f'REVOKE ALL ON SEQUENCES FROM "{runtime}";'
        )
        cursor.execute(f'REVOKE ALL PRIVILEGES ON ALL TABLES IN SCHEMA public FROM "{runtime}";')
        cursor.execute(f'REVOKE ALL PRIVILEGES ON ALL SEQUENCES IN SCHEMA public FROM "{runtime}";')
        cursor.execute(f'REVOKE USAGE ON SCHEMA public FROM "{runtime}";')
        cursor.execute(f'REVOKE "{runtime}" FROM "{proprietaire}";')
        cursor.execute(f'DROP OWNED BY "{runtime}";')
        cursor.execute(f'DROP ROLE IF EXISTS "{runtime}";')
    logger.info("Rôle Postgres restreint '%s' supprimé.", runtime)


def _doit_garder_role_proprietaire() -> bool:
    """True si la commande `manage.py` en cours a besoin des pleins pouvoirs
    du rôle propriétaire (DDL, ou construction de la base de test) — voir
    `COMMANDES_ROLE_PROPRIETAIRE`."""
    return len(sys.argv) > 1 and sys.argv[1] in COMMANDES_ROLE_PROPRIETAIRE


def activer_isolement_runtime(sender: object, connection: BaseDatabaseWrapper, **kwargs: object) -> None:
    """Receiver de `connection_created` : bascule la session Postgres neuve
    sur le rôle `_runtime`, sauf pour les commandes qui ont besoin du rôle
    propriétaire (voir `COMMANDES_ROLE_PROPRIETAIRE`).

    `SET ROLE` (et non `SET LOCAL ROLE`) : l'isolement doit tenir pour toute
    la durée de vie de la connexion — `grpc_server` est un process long, pas
    une requête HTTP unique — contrairement à `SET LOCAL` qui s'annulerait à
    la fin de la première transaction.
    """
    if connection.vendor != "postgresql":
        return
    if _doit_garder_role_proprietaire():
        return
    runtime = role_runtime(connection)
    if runtime is None:
        return
    with connection.cursor() as cursor:
        cursor.execute(f'SET ROLE "{runtime}";')
    logger.debug("Connexion Postgres basculée sur le rôle restreint '%s'.", runtime)


def connecter_isolement_runtime() -> None:
    """À appeler une fois depuis `settings.py`, juste après `DATABASES`.

    Idempotent côté Django (`Signal.connect` déduplique par
    `(receiver, sender, dispatch_uid)` — ici `dispatch_uid` fixe pour éviter
    un double abonnement si `settings.py` était réimporté)."""
    connection_created.connect(activer_isolement_runtime, dispatch_uid="sgfe_common.db_hardening")
