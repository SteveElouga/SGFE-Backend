"""Bout en bout : le pipeline RÉEL de journalisation chaînée détecte une
altération — voir AUDIT_SGFE.md §J "Journalisation de sécurité centralisée
et inviolable".

`tests/test_log_integrity.py` (existant) vérifie `ChainedHashFormatter.format()`
appelé directement, en isolation — jamais câblé via `logging.config.dictConfig`
comme le fait réellement `notification/settings.py`, jamais écrit sur un vrai
`FileHandler`, et jamais relu pour vérifier la détection d'altération
(`verifier_chaine_logs.py`, `libs/sgfe_common/`, testé exhaustivement mais
seulement en pur unittest côté source canonique — jamais exercé depuis un
service). Ce fichier ferme cet écart : configure un logger avec EXACTEMENT le
formatter et le format de `settings.LOGGING["formatters"]["iso8601_chained"]`,
écrit de vrais enregistrements sur un vrai fichier temporaire via de vrais
appels `logger.warning(...)` (propagation + `Handler.emit()` réels, pas
`formatter.format()` appelé à la main), puis relit le fichier et vérifie la
chaîne — intacte, puis délibérément rompue.

La logique de vérification est réimplémentée localement (même algorithme que
`libs/sgfe_common/sgfe_common/verifier_chaine_logs.py::verifier_chaine`)
plutôt qu'importée depuis `libs/` : chaque service reste un module autonome
qui ne dépend jamais du dépôt monorepo au runtime (voir la docstring de
`notifications/log_integrity.py`, "Pourquoi pas un vrai package Python
importé") — copier le algorithme de vérification suit le même principe que
la copie du formatter lui-même.
"""

from __future__ import annotations

import hashlib
import logging
import logging.config
import tempfile
from pathlib import Path

from django.conf import settings
from django.test import TestCase

from notifications.log_integrity import GENESIS_HASH, LOG_HASH_SUFFIX_RE


def _decouper_en_enregistrements(texte: str) -> list[str]:
    """Même découpage que `verifier_chaine_logs.py` — un enregistrement peut
    s'étaler sur plusieurs lignes physiques (trace d'exception)."""
    enregistrements: list[str] = []
    tampon: list[str] = []
    for ligne in texte.split("\n"):
        tampon.append(ligne)
        if LOG_HASH_SUFFIX_RE.match(ligne):
            enregistrements.append("\n".join(tampon))
            tampon = []
    return enregistrements


def _verifier_chaine(texte: str) -> tuple[bool, int]:
    """Réimplémentation locale de `verifier_chaine_logs.verifier_chaine` —
    renvoie (intact, nombre d'enregistrements vérifiés avant toute rupture)."""
    enregistrements = _decouper_en_enregistrements(texte)
    if not enregistrements:
        return False, 0
    hash_precedent = GENESIS_HASH
    for numero, enregistrement in enumerate(enregistrements, start=1):
        correspondance = LOG_HASH_SUFFIX_RE.match(enregistrement)
        assert correspondance is not None
        contenu = correspondance.group("content")
        hash_attendu = correspondance.group("hash")
        hash_recalcule = hashlib.sha256((hash_precedent + contenu).encode("utf-8")).hexdigest()
        if hash_recalcule != hash_attendu:
            return False, numero - 1
        hash_precedent = hash_attendu
    return True, len(enregistrements)


class PipelineChainageLogsReelTests(TestCase):
    """Configure le VRAI formatter (`ChainedHashFormatter`) via
    `logging.config.dictConfig`, exactement comme `notification/settings.py`
    le fait pour le handler "file" — mais isolé sur un fichier temporaire et
    un logger dédié, pour ne jamais interférer avec la config globale des
    autres tests."""

    def setUp(self) -> None:
        self.fichier_temp = tempfile.NamedTemporaryFile(mode="w", suffix=".log", delete=False)
        self.fichier_temp.close()
        self.chemin = Path(self.fichier_temp.name)
        self.logger_name = "notifications.tests.chainage_reel"

        # Même format que settings.LOGGING["formatters"]["iso8601_chained"] —
        # lu directement depuis la config réelle pour ne jamais diverger
        # silencieusement si le format change un jour côté settings.py.
        format_reel = settings.LOGGING["formatters"]["iso8601_chained"]["format"]  # type: ignore[index]
        datefmt_reel = settings.LOGGING["formatters"]["iso8601_chained"]["datefmt"]  # type: ignore[index]

        logging.config.dictConfig(
            {
                "version": 1,
                "disable_existing_loggers": False,
                "formatters": {
                    "chaine_test": {
                        "()": "notifications.log_integrity.ChainedHashFormatter",
                        "format": format_reel,
                        "datefmt": datefmt_reel,
                    },
                },
                "handlers": {
                    "fichier_test": {
                        "class": "logging.FileHandler",
                        "filename": str(self.chemin),
                        "formatter": "chaine_test",
                    },
                },
                "loggers": {
                    self.logger_name: {
                        "handlers": ["fichier_test"],
                        "level": "INFO",
                        "propagate": False,
                    },
                },
            }
        )
        self.logger = logging.getLogger(self.logger_name)

    def tearDown(self) -> None:
        for handler in list(self.logger.handlers):
            handler.close()
            self.logger.removeHandler(handler)
        self.chemin.unlink(missing_ok=True)

    def test_plusieurs_enregistrements_reels_forment_une_chaine_intacte(self) -> None:
        """Écrit 4 lignes via de vrais appels logger.*(), à travers le VRAI
        pipeline logging (propagation, Handler.emit(), pas formatter.format()
        appelé directement) — puis vérifie la chaîne complète."""
        self.logger.info("Diffusion créée")
        self.logger.warning("Échec envoi WhatsApp facture=abc123")
        self.logger.info("Token révoqué token_id=xyz")
        self.logger.error("RetryEnvoisEchecJob échoué : boom")

        texte = self.chemin.read_text(encoding="utf-8")
        intact, nb_verifies = _verifier_chaine(texte)

        self.assertTrue(intact)
        self.assertEqual(nb_verifies, 4)
        # Chaque ligne porte bien le suffixe attendu par un futur auditeur.
        self.assertEqual(texte.count(" log_hash="), 4)

    def test_ligne_modifiee_au_milieu_rompt_la_chaine_a_partir_de_cette_ligne(self) -> None:
        """La propriété centrale du mécanisme : modifier UNE ligne casse le
        calcul de hash de TOUTES celles qui suivent — un auditeur qui relit
        le fichier détecte immédiatement où la falsification a eu lieu."""
        self.logger.info("Ligne 1 — jamais modifiée")
        self.logger.info("Ligne 2 — sera modifiée après coup")
        self.logger.info("Ligne 3 — jamais modifiée par l'attaquant")

        lignes = self.chemin.read_text(encoding="utf-8").splitlines()
        self.assertEqual(len(lignes), 3)
        # Modifie le CONTENU de la ligne 2 sans recalculer son hash — c'est
        # exactement ce qu'un attaquant sans accès au code ferait avec un
        # simple éditeur de texte.
        lignes[1] = lignes[1].replace("sera modifiée après coup", "ATTAQUANT: rien à voir ici")
        self.chemin.write_text("\n".join(lignes) + "\n", encoding="utf-8")

        intact, nb_verifies_avant_rupture = _verifier_chaine(self.chemin.read_text(encoding="utf-8"))

        self.assertFalse(intact)
        # La ligne 1 (non modifiée) reste vérifiable ; la rupture apparaît à
        # la ligne 2, celle qui a été altérée.
        self.assertEqual(nb_verifies_avant_rupture, 1)

    def test_suppression_d_une_ligne_au_milieu_est_detectee(self) -> None:
        """Supprimer une ligne casse la chaîne au même titre que la modifier
        — le hash de la ligne suivante référence un hash précédent qui
        n'existe plus dans le fichier."""
        self.logger.info("Ligne 1")
        self.logger.info("Ligne 2 — sera supprimée")
        self.logger.info("Ligne 3")

        lignes = self.chemin.read_text(encoding="utf-8").splitlines()
        self.assertEqual(len(lignes), 3)
        del lignes[1]
        self.chemin.write_text("\n".join(lignes) + "\n", encoding="utf-8")

        intact, _nb = _verifier_chaine(self.chemin.read_text(encoding="utf-8"))

        self.assertFalse(intact)

    def test_fichier_intact_mais_vide_de_toute_ligne_reconnue_est_signale(self) -> None:
        """Un fichier sans aucune ligne portant le suffixe `log_hash=` (log
        jamais câblé sur ce formatter, ou vidé) ne doit jamais être confondu
        avec une chaîne "intacte" à zéro enregistrement."""
        intact, nb = _verifier_chaine("")
        self.assertFalse(intact)
        self.assertEqual(nb, 0)
