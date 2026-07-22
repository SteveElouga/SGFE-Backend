"""Réconciliation des soldes — recrée les `SoldeFacture` manquants.

Une facture peut naître « orpheline » (sans solde) si le Paiement Service était
indisponible au moment de sa génération : l'initialisation du solde y est en
dégradation gracieuse (voir `factures/services.py`). Sans solde, la facture ne
peut ni être payée ni apparaître correctement dans les impayés.

Cette commande reparcourt toutes les factures et (ré)initialise leur solde via
Paiement Service. C'est sûr car `InitialiserSolde` est **idempotent** : un solde
déjà présent est laissé intact (versements préservés), seuls les soldes manquants
sont créés.

Si le Paiement Service est injoignable, ses appels sont ignorés (dégradation
gracieuse côté client) : la commande le détecte et **échoue explicitement**
plutôt que de faire croire à une réconciliation réussie.

    python manage.py reconcilier_soldes
"""

from django.core.management.base import BaseCommand, CommandError

from factures.grpc_clients import PaiementServiceClient
from factures.repositories import FactureRepository


class Command(BaseCommand):
    help = "Recrée les soldes manquants des factures orphelines (via Paiement Service, idempotent)."

    def handle(self, *args, **options) -> None:
        client = PaiementServiceClient()
        factures = FactureRepository().list_by_filters()  # toutes les factures
        # initialiser_solde renvoie True si OK, False en dégradation gracieuse (paiement KO).
        ok = sum(
            client.initialiser_solde(
                facture_id=str(f.id),
                abonne_id=f.abonne_id,
                montant_total=float(f.montant),
                date_limite_paiement=f.date_limite_paiement.isoformat(),
                campagne_id=f.campagne_id,
            )
            for f in factures
        )
        total = len(factures)
        if ok < total:
            raise CommandError(
                f"{total - ok}/{total} factures NON réconciliées — Paiement Service "
                "injoignable ? Vérifie qu'il tourne (docker compose up -d) puis relance."
            )
        self.stdout.write(
            self.style.SUCCESS(
                f"Réconciliation terminée : {ok}/{total} factures traitées "
                "(soldes manquants recréés, existants inchangés)."
            )
        )
