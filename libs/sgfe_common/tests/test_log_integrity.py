"""Tests de `sgfe_common.log_integrity.ChainedHashFormatter` — voir
AUDIT_SGFE.md §J "Journalisation de sécurité centralisée et inviolable".

Sans dépendance Django (le module testé n'en a aucune) : exécutable
directement, sans venv de service.

    python3 -m unittest discover -s libs/sgfe_common/tests
"""

from __future__ import annotations

import hashlib
import logging
import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from sgfe_common.log_integrity import GENESIS_HASH, LOG_HASH_SUFFIX_RE, ChainedHashFormatter


def _record(message: str) -> logging.LogRecord:
    """Fabrique un `LogRecord` minimal, sans passer par un vrai logger."""
    return logging.LogRecord(
        name="test.logger",
        level=logging.WARNING,
        pathname=__file__,
        lineno=1,
        msg=message,
        args=(),
        exc_info=None,
    )


class ChainedHashFormatterTests(unittest.TestCase):
    def setUp(self) -> None:
        self.formatter = ChainedHashFormatter("%(levelname)s %(message)s")

    def test_premiere_ligne_chainee_depuis_genesis_hash(self) -> None:
        sortie = self.formatter.format(_record("première ligne"))

        correspondance = LOG_HASH_SUFFIX_RE.match(sortie)
        assert correspondance is not None
        contenu, hash_obtenu = correspondance.group("content"), correspondance.group("hash")
        self.assertEqual(contenu, "WARNING première ligne")
        attendu = hashlib.sha256((GENESIS_HASH + contenu).encode("utf-8")).hexdigest()
        self.assertEqual(hash_obtenu, attendu)

    def test_deuxieme_ligne_chainee_depuis_le_hash_de_la_premiere(self) -> None:
        sortie1 = self.formatter.format(_record("ligne 1"))
        sortie2 = self.formatter.format(_record("ligne 2"))

        hash1 = LOG_HASH_SUFFIX_RE.match(sortie1).group("hash")  # type: ignore[union-attr]
        correspondance2 = LOG_HASH_SUFFIX_RE.match(sortie2)
        assert correspondance2 is not None
        contenu2, hash2 = correspondance2.group("content"), correspondance2.group("hash")
        attendu = hashlib.sha256((hash1 + contenu2).encode("utf-8")).hexdigest()
        self.assertEqual(hash2, attendu)
        self.assertNotEqual(hash1, hash2)

    def test_deux_instances_ont_des_chaines_independantes(self) -> None:
        """Deux formatters distincts (ex. deux handlers, deux formatter NOMMÉS
        différemment dans dictConfig) ne doivent jamais interférer — voir la
        docstring du module sur le partage d'instance entre handlers."""
        autre_formatter = ChainedHashFormatter("%(levelname)s %(message)s")

        sortie_a = self.formatter.format(_record("même contenu"))
        sortie_b = autre_formatter.format(_record("même contenu"))

        # Même contenu, même GENESIS_HASH de départ pour chaque instance
        # indépendante : les deux hash doivent donc être identiques ici —
        # la preuve d'indépendance vient du test suivant (deuxième appel).
        self.assertEqual(
            LOG_HASH_SUFFIX_RE.match(sortie_a).group("hash"),  # type: ignore[union-attr]
            LOG_HASH_SUFFIX_RE.match(sortie_b).group("hash"),  # type: ignore[union-attr]
        )

        # `self.formatter` avance sa propre chaîne ; `autre_formatter` ne
        # doit pas en être affecté.
        self.formatter.format(_record("avance la chaîne de self.formatter"))
        sortie_b2 = autre_formatter.format(_record("même contenu"))
        hash_b1 = LOG_HASH_SUFFIX_RE.match(sortie_b).group("hash")  # type: ignore[union-attr]
        hash_b2 = LOG_HASH_SUFFIX_RE.match(sortie_b2).group("hash")  # type: ignore[union-attr]
        # `autre_formatter` a bien avancé (b2 dépend de b1), pas de la chaîne
        # de `self.formatter` : recalculé indépendamment pour le prouver.
        attendu_b2 = hashlib.sha256((hash_b1 + "WARNING même contenu").encode("utf-8")).hexdigest()
        self.assertEqual(hash_b2, attendu_b2)

    def test_message_avec_accents_et_utf8(self) -> None:
        """Le hash doit être stable pour du contenu non-ASCII (messages en
        français — voir la convention de commentaires du dépôt)."""
        sortie = self.formatter.format(_record("Accès refusé : rôle éphémère"))
        self.assertIsNotNone(LOG_HASH_SUFFIX_RE.match(sortie))


if __name__ == "__main__":
    unittest.main()
