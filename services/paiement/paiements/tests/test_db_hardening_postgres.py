"""Preuve, contre un Postgres RÉEL, que le rôle `_runtime` bloque bien
`UPDATE`/`DELETE` sur `audit_log` — là où le rôle propriétaire d'origine ne
le pouvait pas malgré `0013_audit_log_immutable` (voir AUDIT_SGFE.md §8·J et
`paiements/db_hardening.py` pour le constat empirique complet : le rôle de
connexion est un SUPERUTILISATEUR Postgres, qui contourne tout REVOKE tant
qu'aucun `SET ROLE` ne bascule la session sur un rôle non superutilisateur).

Gaté par `FORCE_POSTGRES_TESTS` (comme le job CI `test-paiement`, qui fait
déjà tourner cette suite contre un `postgres:16-alpine` jetable — voir
`.github/workflows/ci.yml`) : no-op sur SQLite, le moteur des tests locaux
par défaut (`TESTING`, voir `settings.py`), qui n'a ni rôles ni REVOKE/GRANT.

`manage.py test` fait partie de `COMMANDES_ROLE_PROPRIETAIRE`
(`db_hardening.py`) : la connexion de test reste donc sous le rôle
propriétaire tout du long (migrations 0012-0015 appliquées avec les pleins
pouvoirs, comme en production). Le rôle `_runtime` créé par 0015 existe donc
déjà quand ce test s'exécute — il ne reste qu'à y basculer manuellement via
`SET ROLE`, exactement ce que ferait `activer_isolement_runtime` pour une
connexion `grpc_server` réelle.
"""

from __future__ import annotations

import uuid
from unittest import skipUnless

from django.db import connection, transaction
from django.db.utils import ProgrammingError
from django.test import TestCase

from paiements.db_hardening import role_runtime
from paiements.models import AuditLog

_SUR_POSTGRESQL = connection.vendor == "postgresql"
_RAISON_SKIP = "nécessite un vrai Postgres (FORCE_POSTGRES_TESTS=True) — no-op sur SQLite"


@skipUnless(_SUR_POSTGRESQL, _RAISON_SKIP)
class RoleRuntimeBloqueReellementUpdateDeleteTests(TestCase):
    """Contre-exemple direct de ce que `0013_audit_log_immutable` documentait
    comme limite : ici, `UPDATE`/`DELETE` échouent VRAIMENT."""

    def setUp(self) -> None:
        self._runtime = role_runtime(connection)
        assert self._runtime is not None  # garanti par le skipUnless ci-dessus
        self.entry = AuditLog.objects.create(
            action="TEST_AUDIT",
            objet_type="Test",
            objet_id=str(uuid.uuid4()),
            detail="ligne de test — jamais modifiée si le rôle _runtime tient sa promesse",
        )

    def tearDown(self) -> None:
        # Restaure le rôle de connexion d'origine (superutilisateur) — la
        # connexion est réutilisée par les tests suivants du même run.
        with connection.cursor() as cursor:
            cursor.execute("RESET ROLE;")

    def _set_role_runtime(self) -> None:
        with connection.cursor() as cursor:
            cursor.execute(f'SET ROLE "{self._runtime}";')

    def test_le_role_proprietaire_peut_encore_modifier_avant_set_role(self) -> None:
        """Comportement historique (celui que documentait 0013) : sans
        `SET ROLE`, la connexion reste le rôle propriétaire/superutilisateur
        — la révocation de 0013 ne le bloque pas. Ce test fige ce fait pour
        qu'un futur lecteur voie le AVANT/APRÈS, pas seulement l'APRÈS."""
        AuditLog.objects.filter(id=self.entry.id).update(detail="modifié par le rôle propriétaire")
        self.entry.refresh_from_db()
        self.assertEqual(self.entry.detail, "modifié par le rôle propriétaire")

    def test_update_echoue_avec_permission_denied_apres_set_role(self) -> None:
        self._set_role_runtime()

        with self.assertRaises(ProgrammingError) as ctx:
            with transaction.atomic():
                AuditLog.objects.filter(id=self.entry.id).update(detail="HACKED")
        self.assertIn("permission denied", str(ctx.exception).lower())

        # La ligne n'a pas bougé — le ROLLBACK TO SAVEPOINT de `atomic()`
        # défait la tentative, et le rôle restreint l'a de toute façon
        # refusée côté serveur avant même ce rollback.
        self.entry.refresh_from_db()
        self.assertNotEqual(self.entry.detail, "HACKED")

    def test_delete_echoue_avec_permission_denied_apres_set_role(self) -> None:
        self._set_role_runtime()

        with self.assertRaises(ProgrammingError) as ctx:
            with transaction.atomic():
                AuditLog.objects.filter(id=self.entry.id).delete()
        self.assertIn("permission denied", str(ctx.exception).lower())

        self.assertTrue(AuditLog.objects.filter(id=self.entry.id).exists())

    def test_select_et_insert_restent_autorises_apres_set_role(self) -> None:
        """Le rôle `_runtime` n'est pas verrouillé à zéro accès — seulement
        `UPDATE`/`DELETE` sur `audit_log` : `enregistrer_audit` (SELECT
        implicite pour l'ORM + INSERT) doit continuer de fonctionner."""
        self._set_role_runtime()

        # SELECT
        self.assertTrue(AuditLog.objects.filter(id=self.entry.id).exists())

        # INSERT — exactement ce que fait `enregistrer_audit` en production.
        nouvelle = AuditLog.objects.create(
            action="TEST_AUDIT_2",
            objet_type="Test",
            objet_id=str(uuid.uuid4()),
            detail="créée sous le rôle _runtime",
        )
        self.assertTrue(AuditLog.objects.filter(id=nouvelle.id).exists())

    def test_role_runtime_n_est_pas_superutilisateur(self) -> None:
        """Vérifie la cause racine documentée dans `db_hardening.py` : ce
        n'est pas qu'un blocage au niveau de la table, la session perd
        réellement son statut superutilisateur après `SET ROLE`."""
        self._set_role_runtime()

        with connection.cursor() as cursor:
            cursor.execute("SELECT rolsuper FROM pg_roles WHERE rolname = current_user;")
            (est_superutilisateur,) = cursor.fetchone()

        self.assertFalse(est_superutilisateur)
