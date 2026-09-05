"""Tests du chaînage de hash tamper-evident des logs — voir
AUDIT_SGFE.md §J "Journalisation de sécurité centralisée et inviolable".

`comptes/log_integrity.py` est une copie synchronisée de
`libs/sgfe_common/sgfe_common/log_integrity.py` (voir
`scripts/sync-log-integrity-lib.sh`, testé exhaustivement côté canonique
dans `libs/sgfe_common/tests/`) : ce fichier se limite donc à vérifier que la
copie fonctionne à l'identique ET que le câblage dans `LOGGING`
(`auth/settings.py`) pointe bien vers elle, sur le bon handler.
"""

from __future__ import annotations

import hashlib
import logging

from django.conf import settings
from django.test import SimpleTestCase

from comptes.log_integrity import GENESIS_HASH, LOG_HASH_SUFFIX_RE, ChainedHashFormatter


def _record(message: str) -> logging.LogRecord:
    return logging.LogRecord(
        name="test.logger", level=logging.WARNING, pathname=__file__, lineno=1, msg=message, args=(), exc_info=None
    )


class ChainedHashFormatterTests(SimpleTestCase):
    def test_chaine_deux_lignes_correctement(self) -> None:
        formatter = ChainedHashFormatter("%(message)s")
        sortie1 = formatter.format(_record("ligne 1"))
        sortie2 = formatter.format(_record("ligne 2"))

        correspondance1 = LOG_HASH_SUFFIX_RE.match(sortie1)
        correspondance2 = LOG_HASH_SUFFIX_RE.match(sortie2)
        assert correspondance1 is not None
        assert correspondance2 is not None

        hash1_attendu = hashlib.sha256((GENESIS_HASH + correspondance1.group("content")).encode("utf-8")).hexdigest()
        self.assertEqual(correspondance1.group("hash"), hash1_attendu)
        hash2_attendu = hashlib.sha256((hash1_attendu + correspondance2.group("content")).encode("utf-8")).hexdigest()
        self.assertEqual(correspondance2.group("hash"), hash2_attendu)


class LoggingSettingsWiringTests(SimpleTestCase):
    """Vérifie que le bloc `LOGGING` câble bien `ChainedHashFormatter` sur le
    handler fichier — jamais sur "console" (voir la docstring du module pour
    la raison : deux handlers partageant la même instance interféreraient)."""

    def test_formatter_chaine_pointe_vers_comptes_log_integrity(self) -> None:
        # `settings.LOGGING` est typé `dict[str, object]` (valeurs de forme
        # libre, cohérent avec le schéma `logging.config.dictConfig`) —
        # `isinstance` affine le type plutôt qu'un `cast`, pour ne jamais
        # introduire `Any` (voir les contraintes de typage du dépôt).
        formatters = settings.LOGGING["formatters"]
        assert isinstance(formatters, dict)
        formatter_chaine = formatters["iso8601_chained"]
        assert isinstance(formatter_chaine, dict)
        self.assertEqual(formatter_chaine["()"], "comptes.log_integrity.ChainedHashFormatter")

    def test_handler_console_n_utilise_pas_le_formatter_chaine(self) -> None:
        # Le handler "file" lui-même n'existe PAS dans `settings.LOGGING`
        # tant que `TESTING=True` (voir `auth/settings.py` : ajouté seulement
        # `if not TESTING`, pour ne jamais écrire sur disque pendant
        # `manage.py test`) — non testable ici pour cette raison. Câblage
        # vérifié manuellement en mode non-test (voir la description de la
        # PR) : le handler "file" utilise bien "iso8601_chained".
        handlers = settings.LOGGING["handlers"]
        assert isinstance(handlers, dict)
        handler_console = handlers["console"]
        assert isinstance(handler_console, dict)
        self.assertEqual(handler_console["formatter"], "iso8601")
