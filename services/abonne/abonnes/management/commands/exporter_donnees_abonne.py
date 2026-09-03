"""Export RGPD structuré de toutes les données connues d'un abonné (droit à
la portabilité) — voir `abonnes/export.py` pour le détail des sections et la
dégradation gracieuse par service externe indisponible.

    python manage.py exporter_donnees_abonne <abonne_id>
    python manage.py exporter_donnees_abonne <abonne_id> --output export.json

Sans `--output`, le JSON (structuré, indenté) est écrit sur stdout.
"""

from django.core.exceptions import ObjectDoesNotExist
from django.core.management.base import BaseCommand, CommandError

from abonnes.export import exporter_donnees_abonne_json


class Command(BaseCommand):
    help = "Exporte en JSON structuré toutes les données connues d'un abonné (droit à la portabilité RGPD)."

    def add_arguments(self, parser) -> None:
        parser.add_argument("abonne_id", help="UUID de l'abonné à exporter")
        parser.add_argument(
            "--output",
            help="Chemin du fichier de sortie (défaut : affichage sur stdout)",
        )

    def handle(self, *args, **options) -> None:
        abonne_id = options["abonne_id"]
        try:
            payload = exporter_donnees_abonne_json(abonne_id)
        except ObjectDoesNotExist as exc:
            raise CommandError(f"Abonné introuvable : {abonne_id}") from exc

        output = options.get("output")
        if output:
            with open(output, "w", encoding="utf-8") as fichier:
                fichier.write(payload)
            self.stdout.write(self.style.SUCCESS(f"Export de l'abonné {abonne_id} écrit dans {output}"))
        else:
            self.stdout.write(payload)
