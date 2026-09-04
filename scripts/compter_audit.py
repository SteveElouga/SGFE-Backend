#!/usr/bin/env python3
"""Recompte les items de la checklist §8 d'AUDIT_SGFE.md, par priorité et par
statut (Fait / Partiel / Incertain / Non fait), pour vérifier la cohérence du
tableau récapitulatif ("Décompte") après toute mise à jour manuelle de la
checklist — la classification se fait par l'émoji de statut porté par chaque
ligne (✅/🟡/❓), pas par la case à cocher Markdown elle-même : plusieurs items
non cochés portent un statut 🟡/❓ plutôt que "Non fait" pur (voir §K/§O/§P).

Usage :
    python3 scripts/compter_audit.py [chemin_vers_AUDIT_SGFE.md]

Sans argument, lit ``AUDIT_SGFE.md`` à la racine du dépôt courant.
"""

import re
import sys
from pathlib import Path

PRIORITY_HEADERS = {
    "🔴 P0": "### 🔴 P0",
    "🟠 P1": "### 🟠 P1",
    "🟡 P2": "### 🟡 P2",
    "🟢 P3": "### 🟢 P3",
}
CHECKLIST_END_MARKER = "### Portes de sortie"
STATUSES = ("Fait", "Partiel", "Incertain", "Non fait")


def _classify(item_text: str) -> str:
    """Retourne le statut d'un item de checklist à partir de son émoji.

    Ordre de priorité ✅ > 🟡 > ❓ : un item peut mentionner plusieurs emojis
    dans son commentaire (ex. une preuve détaillée citant un autre item), seul
    le premier statut trouvé dans cet ordre qualifie la ligne elle-même.
    """
    if "✅" in item_text:
        return "Fait"
    if "🟡" in item_text:
        return "Partiel"
    if "❓" in item_text:
        return "Incertain"
    return "Non fait"


def count_checklist(path: Path) -> dict[str, dict[str, int]]:
    """Compte les items de checklist de chaque section de priorité.

    Retourne ``{nom_priorite: {statut: nombre}}`` — lève ``ValueError`` si les
    marqueurs de section attendus sont introuvables (le document a changé de
    structure, mieux vaut échouer bruyamment qu'afficher un compte silencieusement
    faux).
    """
    text = path.read_text(encoding="utf-8")

    start = text.index("### 🔴 P0")
    end = text.index(CHECKLIST_END_MARKER)
    section = text[start:end]

    positions = sorted(
        (section.index(marker), name) for name, marker in PRIORITY_HEADERS.items()
    )
    bounds = [pos for pos, _ in positions] + [len(section)]
    blocks = {
        name: section[bounds[i] : bounds[i + 1]]
        for i, (_, name) in enumerate(positions)
    }

    results: dict[str, dict[str, int]] = {}
    for name, block in blocks.items():
        items = re.findall(r"^- \[( |x)\] (.+)$", block, flags=re.MULTILINE)
        counts = {status: 0 for status in STATUSES}
        for _checkbox, item_text in items:
            counts[_classify(item_text)] += 1
        results[name] = counts
    return results


def print_report(results: dict[str, dict[str, int]]) -> None:
    """Affiche le tableau par priorité puis le total général, en pourcentages."""
    header = f"{'Priorité':8} {'Total':>6} {'Fait':>6} {'Partiel':>8} {'Incertain':>10} {'Non fait':>9}"
    print(header)

    total: dict[str, int] = {status: 0 for status in STATUSES}
    for name, counts in results.items():
        subtotal = sum(counts.values())
        for status in STATUSES:
            total[status] += counts[status]
        print(
            f"{name:8} {subtotal:>6} {counts['Fait']:>6} {counts['Partiel']:>8} {counts['Incertain']:>10} {counts['Non fait']:>9}"
        )

    grand_total = sum(total.values())
    print("-" * 60)
    print(
        f"{'Total':8} {grand_total:>6} {total['Fait']:>6} {total['Partiel']:>8} "
        f"{total['Incertain']:>10} {total['Non fait']:>9}"
    )
    print()
    print(f"Total items       : {grand_total}")
    for status in STATUSES:
        n = total[status]
        pct = round(100 * n / grand_total) if grand_total else 0
        print(f"{status:<18}: {n} ({pct} %)")


if __name__ == "__main__":
    audit_path = Path(sys.argv[1]) if len(sys.argv) > 1 else Path("AUDIT_SGFE.md")
    print_report(count_checklist(audit_path))
