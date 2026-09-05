"""Tests de `sgfe_common.verifier_chaine_logs` — voir AUDIT_SGFE.md §J
"Journalisation de sécurité centralisée et inviolable".

    python3 -m unittest discover -s libs/sgfe_common/tests
"""

from __future__ import annotations

import logging
import sys
import tempfile
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from sgfe_common.log_integrity import ChainedHashFormatter
from sgfe_common.verifier_chaine_logs import main, verifier_chaine


def _fabriquer_log_valide(messages: list[str]) -> str:
    """Produit un contenu de log chaîné valide, exactement comme le ferait
    `ChainedHashFormatter` câblé dans un vrai `LOGGING` de settings.py."""
    formatter = ChainedHashFormatter("%(message)s")
    lignes = []
    for message in messages:
        record = logging.LogRecord(
            name="test", level=logging.INFO, pathname=__file__, lineno=1, msg=message, args=(), exc_info=None
        )
        lignes.append(formatter.format(record))
    return "\n".join(lignes) + "\n"


class VerifierChaineTests(unittest.TestCase):
    def test_log_valide_est_verifie_ok(self) -> None:
        texte = _fabriquer_log_valide(["premier événement", "deuxième événement", "troisième événement"])

        intact, message = verifier_chaine(texte)

        self.assertTrue(intact)
        self.assertIn("3 enregistrement", message)

    def test_ligne_modifiee_au_milieu_est_detectee_comme_rompue(self) -> None:
        texte = _fabriquer_log_valide(["a", "b", "c", "d"])
        lignes = texte.splitlines()
        lignes[1] = lignes[1].replace("b log_hash", "b-MODIFIE log_hash")
        texte_modifie = "\n".join(lignes) + "\n"

        intact, message = verifier_chaine(texte_modifie)

        self.assertFalse(intact)
        self.assertIn("ROMPUE", message)
        self.assertIn("2/4", message)  # rompt à l'enregistrement 2

    def test_ligne_supprimee_au_milieu_est_detectee_comme_rompue(self) -> None:
        texte = _fabriquer_log_valide(["a", "b", "c", "d"])
        lignes = texte.splitlines()
        del lignes[1]
        texte_tronque = "\n".join(lignes) + "\n"

        intact, message = verifier_chaine(texte_tronque)

        self.assertFalse(intact)
        self.assertIn("ROMPUE", message)

    def test_derniere_ligne_supprimee_est_detectee(self) -> None:
        """Supprimer la DERNIÈRE ligne ne casse la chaîne d'aucune ligne
        suivante (il n'y en a pas) — mais reste détectable : le nombre
        d'enregistrements vérifiés diffère de ce qu'attend un auditeur qui
        connaît le nombre d'événements réellement émis. Documenté ici comme
        limite : ce script seul ne peut pas deviner qu'une fin de fichier a
        été tronquée sans référence externe (ex. un total attendu)."""
        texte = _fabriquer_log_valide(["a", "b", "c"])
        lignes = texte.splitlines()[:-1]
        texte_tronque = "\n".join(lignes) + "\n"

        intact, message = verifier_chaine(texte_tronque)

        # La chaîne interne reste cohérente (rien après la coupe à vérifier) :
        # exactement la limite honnête documentée dans log_integrity.py.
        self.assertTrue(intact)
        self.assertIn("2 enregistrement", message)

    def test_fichier_vide_est_signale_comme_invalide(self) -> None:
        intact, message = verifier_chaine("")
        self.assertFalse(intact)
        self.assertIn("Aucun enregistrement", message)

    def test_cli_renvoie_0_pour_un_fichier_intact_et_1_pour_un_fichier_rompu(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            fichier = Path(tmp) / "test.log"
            fichier.write_text(_fabriquer_log_valide(["x", "y"]))
            self.assertEqual(main([str(fichier)]), 0)

            lignes = fichier.read_text().splitlines()
            lignes[0] = lignes[0].replace("x log_hash", "x-MODIFIE log_hash")
            fichier.write_text("\n".join(lignes) + "\n")
            self.assertEqual(main([str(fichier)]), 1)

    def test_cli_renvoie_1_pour_un_fichier_introuvable(self) -> None:
        self.assertEqual(main(["/chemin/inexistant/x.log"]), 1)


if __name__ == "__main__":
    unittest.main()
