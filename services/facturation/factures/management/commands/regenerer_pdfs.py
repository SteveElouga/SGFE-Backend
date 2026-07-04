"""Régénère les PDF de factures avec le gabarit courant.

Après une évolution du gabarit `facture_pdf.html`, les PDF déjà stockés (donc
envoyés en WhatsApp / téléchargeables) restent figés sur l'ancien rendu.
`FactureService.get_pdf_bytes` les régénère déjà à la demande (comparaison avec
`PDF_TEMPLATE_VERSION`), mais cette commande permet de tout rafraîchir d'un seul
coup — et surtout de **voir** les échecs de rendu (ex. WeasyPrint indisponible)
plutôt que de les découvrir facture par facture.

Exemples :
    python manage.py regenerer_pdfs --dry-run     # liste les factures obsolètes
    python manage.py regenerer_pdfs               # régénère les obsolètes
    python manage.py regenerer_pdfs --all         # régénère tout, même à jour
    python manage.py regenerer_pdfs --before 2026-07-03
    python manage.py regenerer_pdfs --limit 50
"""

import datetime

from django.core.management.base import BaseCommand, CommandError

from factures.models import Facture
from factures.pdf_generator import PDF_TEMPLATE_VERSION
from factures.services import FactureService


class Command(BaseCommand):
    help = "Régénère les PDF de factures avec le gabarit courant (rendu à jour)."

    def add_arguments(self, parser) -> None:
        parser.add_argument(
            "--all",
            action="store_true",
            help="Régénère toutes les factures, y compris celles déjà à la version courante.",
        )
        parser.add_argument(
            "--before",
            type=str,
            default="",
            help="Ne traiter que les factures générées avant cette date (YYYY-MM-DD).",
        )
        parser.add_argument(
            "--limit",
            type=int,
            default=0,
            help="Nombre maximum de factures à traiter (0 = pas de limite).",
        )
        parser.add_argument(
            "--dry-run",
            action="store_true",
            help="Liste les factures concernées sans rien régénérer.",
        )

    def handle(self, *args, **options) -> None:
        qs = Facture.objects.all().order_by("date_generation")
        if not options["all"]:
            qs = qs.exclude(pdf_template_version=PDF_TEMPLATE_VERSION)
        if options["before"]:
            try:
                cutoff = datetime.date.fromisoformat(options["before"])
            except ValueError as exc:
                raise CommandError(f"--before : date invalide « {options['before']} » (attendu YYYY-MM-DD).") from exc
            qs = qs.filter(date_generation__date__lt=cutoff)
        if options["limit"] > 0:
            qs = qs[: options["limit"]]

        factures = list(qs)
        total = len(factures)
        if total == 0:
            self.stdout.write(self.style.SUCCESS("Aucune facture à régénérer."))
            return

        self.stdout.write(f"{total} facture(s) à régénérer (version cible : {PDF_TEMPLATE_VERSION}).")

        if options["dry_run"]:
            for facture in factures:
                self.stdout.write(
                    f"  [dry-run] {facture.numero_facture} (version stockée : {facture.pdf_template_version})"
                )
            self.stdout.write(self.style.WARNING("Dry-run : aucune régénération effectuée."))
            return

        service = FactureService()
        # La société (identité affichée sur le PDF) est la même pour tout le lot :
        # récupérée une seule fois pour éviter un appel gRPC Config par facture.
        from factures.grpc_clients import ConfigServiceClient

        societe = ConfigServiceClient().get_infos_societe()

        succes = 0
        echecs: list[str] = []
        for facture in factures:
            if service.regenerer_pdf(facture, societe=societe):
                succes += 1
            else:
                echecs.append(facture.numero_facture)

        self.stdout.write(self.style.SUCCESS(f"{succes}/{total} PDF régénérés."))
        if echecs:
            self.stdout.write(self.style.ERROR(f"{len(echecs)} échec(s) : {', '.join(echecs)}"))
            self.stdout.write("Vérifie que WeasyPrint et ses bibliothèques natives (pango/cairo) sont disponibles.")
