"""Réconciliation des soldes — recrée les `SoldeFacture` manquants.

Une facture peut naître « orpheline » (sans solde) si le Paiement Service était
indisponible au moment de sa génération : l'initialisation du solde y est en
dégradation gracieuse (voir `factures/services.py`). Sans solde, la facture ne
peut ni être payée ni apparaître correctement dans les impayés.

Cette commande reparcourt toutes les factures et (ré)initialise leur solde via
Paiement Service. C'est sûr car `InitialiserSolde` est **idempotent** : un solde
déjà présent est laissé intact (versements préservés), seuls les soldes manquants
sont créés.

⚠️ À lancer quand le Paiement Service est joignable (sinon les appels sont
ignorés en dégradation gracieuse et rien n'est recréé).

    python manage.py reconcilier_soldes
"""

from django.core.management.base import BaseCommand

from factures.grpc_clients import PaiementServiceClient
from factures.repositories import FactureRepository


class Command(BaseCommand):
    help = "Recrée les soldes manquants des factures orphelines (via Paiement Service, idempotent)."

    def handle(self, *args, **options) -> None:
        client = PaiementServiceClient()
        factures = FactureRepository().list_by_filters()  # toutes les factures
        for facture in factures:
            client.initialiser_solde(
                facture_id=str(facture.id),
                abonne_id=facture.abonne_id,
                montant_total=float(facture.montant),
                date_limite_paiement=facture.date_limite_paiement.isoformat(),
                campagne_id=facture.campagne_id,
            )
        self.stdout.write(
            self.style.SUCCESS(
                f"Réconciliation terminée : {len(factures)} factures traitées "
                "(soldes manquants recréés, existants inchangés)."
            )
        )
