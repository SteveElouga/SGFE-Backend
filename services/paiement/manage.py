#!/usr/bin/env python
"""Point d'entrée de gestion Django du Paiement Service."""

import os
import sys


def main() -> None:
    """Exécute les tâches administratives Django."""
    os.environ.setdefault("DJANGO_SETTINGS_MODULE", "paiement.settings")
    try:
        from django.core.management import execute_from_command_line
    except ImportError as exc:
        raise ImportError(
            "Impossible d'importer Django. Assurez-vous que Django est installé "
            "et disponible dans votre variable d'environnement PYTHONPATH."
        ) from exc
    execute_from_command_line(sys.argv)


if __name__ == "__main__":
    main()
