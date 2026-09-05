#!/usr/bin/env python3
"""Vérifie la chaîne de hash d'un fichier de log produit par
`ChainedHashFormatter` (voir `log_integrity.py`, même dossier) — outil pour
un futur auditeur SOC 2, voir AUDIT_SGFE.md §J "Journalisation de sécurité
centralisée et inviolable".

Ce que ce script détecte : toute ligne modifiée ou supprimée AU MILIEU ou À
LA FIN du fichier depuis son écriture (voir `log_integrity.py` pour le
mécanisme et ses limites honnêtes — ce script ne fait QUE lire et vérifier,
il ne protège rien par lui-même).

Usage :
    python3 libs/sgfe_common/sgfe_common/verifier_chaine_logs.py <fichier_de_log>
    python3 libs/sgfe_common/sgfe_common/verifier_chaine_logs.py <fichier> --hash-initial <hex>

Code de sortie : 0 si la chaîne est intacte, 1 si elle est rompue (ou si le
fichier est illisible/vide de tout enregistrement reconnu).
"""

from __future__ import annotations

import argparse
import hashlib
import sys
from pathlib import Path

# Import de la source canonique — fonctionne quel que soit le répertoire
# d'exécution en ajoutant le dossier PARENT de `sgfe_common` à `sys.path`
# (même motif que `sys.path.insert(0, str(Path(settings.BASE_DIR) / "proto"))`
# utilisé côté services pour les stubs gRPC générés).
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from sgfe_common.log_integrity import GENESIS_HASH, LOG_HASH_SUFFIX_RE  # noqa: E402


def _decouper_en_enregistrements(texte: str) -> list[str]:
    """Regroupe les lignes physiques du fichier en enregistrements logiques.

    Un enregistrement peut s'étaler sur plusieurs lignes physiques (ex. une
    trace d'exception jointe par `exc_info=True`) : seule sa DERNIÈRE ligne
    porte le suffixe ` log_hash=<hex>`. On accumule donc les lignes jusqu'à
    rencontrer ce suffixe, qui termine l'enregistrement courant.

    Les lignes en fin de fichier qui n'atteignent jamais ce suffixe (fichier
    tronqué en plein milieu d'une écriture, par exemple) sont ignorées —
    elles ne peuvent de toute façon pas être vérifiées.
    """
    enregistrements: list[str] = []
    tampon: list[str] = []
    for ligne in texte.split("\n"):
        tampon.append(ligne)
        if LOG_HASH_SUFFIX_RE.match(ligne):
            enregistrements.append("\n".join(tampon))
            tampon = []
    return enregistrements


def verifier_chaine(texte: str, hash_initial: str = GENESIS_HASH) -> tuple[bool, str]:
    """Vérifie la chaîne de hash d'un contenu de log déjà lu.

    `hash_initial` : hash "précédent" supposé avant le premier enregistrement
    du texte fourni — `GENESIS_HASH` par défaut (segment démarré à froid,
    voir `log_integrity.py`). Pour un fichier hérité d'une rotation en cours
    de vie du processus (voir la limite documentée dans `log_integrity.py`),
    passer le dernier hash connu du segment précédent permet de vérifier la
    continuité malgré tout.

    Renvoie `(intact, message)` — `message` décrit le résultat, y compris en
    cas de succès (nombre de lignes vérifiées).
    """
    enregistrements = _decouper_en_enregistrements(texte)
    if not enregistrements:
        return False, "Aucun enregistrement reconnu (fichier vide, ou aucune ligne ne porte 'log_hash=')."

    hash_precedent = hash_initial
    for numero, enregistrement in enumerate(enregistrements, start=1):
        correspondance = LOG_HASH_SUFFIX_RE.match(enregistrement)
        if correspondance is None:  # pragma: no cover — déjà filtré par _decouper_en_enregistrements
            return False, f"Enregistrement {numero} : format inattendu."
        contenu = correspondance.group("content")
        hash_attendu = correspondance.group("hash")
        hash_recalcule = hashlib.sha256((hash_precedent + contenu).encode("utf-8")).hexdigest()
        if hash_recalcule != hash_attendu:
            return False, (
                f"Chaîne ROMPUE à l'enregistrement {numero}/{len(enregistrements)} : "
                f"hash attendu {hash_attendu}, recalculé {hash_recalcule} — "
                "cette ligne (ou une ligne précédente) a été modifiée, ou une ligne a été supprimée."
            )
        hash_precedent = hash_attendu

    return True, f"OK — {len(enregistrements)} enregistrement(s) vérifié(s), chaîne intacte."


def main(argv: list[str] | None = None) -> int:
    """Point d'entrée CLI — lit le fichier passé en argument et affiche le
    verdict. Renvoie le code de sortie process (0 intact, 1 rompu/erreur)."""
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("fichier", type=Path, help="Fichier de log à vérifier")
    parser.add_argument(
        "--hash-initial",
        default=GENESIS_HASH,
        help="Hash précédent supposé avant la première ligne du fichier (défaut : GENESIS_HASH, segment démarré à froid)",
    )
    args = parser.parse_args(argv)

    try:
        texte = args.fichier.read_text(encoding="utf-8")
    except OSError as exc:
        print(f"Impossible de lire {args.fichier} : {exc}", file=sys.stderr)
        return 1

    intact, message = verifier_chaine(texte, hash_initial=args.hash_initial)
    print(message)
    return 0 if intact else 1


if __name__ == "__main__":
    raise SystemExit(main())
