"""Réconciliation des soldes — filet de dernier recours, manuel uniquement.

Depuis l'introduction de l'outbox transactionnelle (voir
`factures/models.py::OutboxEvent` et `factures/schedulers.py::outbox_relay_job`),
CETTE COMMANDE N'EST PLUS LA VOIE DE RATTRAPAGE PRINCIPALE. La création du
`SoldeFacture` est désormais garantie par l'outbox : l'événement
`FACTURE_GENEREE` est écrit dans LA MÊME transaction que la `Facture` (jamais
de facture sans son événement), et le relais planifié le rejoue toutes les 10
secondes jusqu'à ce qu'`InitialiserSolde` réussisse ou que le plafond de
tentatives soit atteint (auquel cas l'événement passe en ECHEC et une alerte
est journalisée — voir `OutboxRelayService.relayer_lot`).

Cette commande reste disponible en filet de tout dernier recours (ex. une
facture antérieure à l'introduction de l'outbox, ou un événement ECHEC qu'on
choisit de rejouer manuellement après avoir corrigé la cause de l'échec) —
volontairement gardée plutôt que supprimée sur un flux financier critique,
mais jamais appelée automatiquement (pas de scheduler, pas de cron : un
opérateur doit la lancer à la main en connaissance de cause).

Reparcourt toutes les factures et (ré)initialise leur solde via Paiement
Service. C'est sûr car `InitialiserSolde` est **idempotent** par `facture_id` :
un solde déjà présent est laissé intact (versements préservés), seuls les
soldes manquants sont créés.

Si le Paiement Service est injoignable, ses appels sont ignorés (dégradation
gracieuse côté client) : la commande le détecte et **échoue explicitement**
plutôt que de faire croire à une réconciliation réussie.

    python manage.py reconcilier_soldes
"""

from typing import Any

from django.core.management.base import BaseCommand, CommandError

from factures.grpc_clients import PaiementServiceClient
from factures.repositories import FactureRepository


class Command(BaseCommand):
    help = "Recrée les soldes manquants des factures orphelines (via Paiement Service, idempotent)."

    def handle(self, *args: Any, **options: Any) -> None:
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
