"""Logique métier du Reporting Service — AgregateurDashboard (ADR-019).

Le côté Query du CQRS : les RPC Update* (déclenchées par les événements des
autres services) mettent à jour les tables dénormalisées ; les lectures
(dashboard, stats) ne font qu'agréger l'existant. Les mises à jour sont
idempotentes autant que possible (upsert par campagne_id).
"""

from dataclasses import dataclass
from decimal import Decimal

from stats.models import StatsCampagne, StatsFacturation, StatsPaiements
from stats.repositories import (
    StatsCampagneRepository,
    StatsFacturationRepository,
    StatsPaiementsRepository,
)

_CENT = Decimal("0.01")


def _dec(value) -> Decimal:
    """Convertit un double proto en Decimal sans erreur de flottant."""
    return Decimal(str(value))


@dataclass
class Dashboard:
    """Snapshot du tableau de bord : stats de la campagne courante (peuvent être None)."""

    campagne: StatsCampagne | None
    facturation: StatsFacturation | None
    paiements: StatsPaiements | None


@dataclass
class StatsGlobales:
    historique_campagnes: list[StatsCampagne]
    consommation_totale_globale: Decimal
    montant_total_facture_global: Decimal
    montant_total_encaisse_global: Decimal


class AgregateurDashboard:
    """Compile les statistiques du tableau de bord et applique les mises à jour."""

    def __init__(self) -> None:
        self._campagne = StatsCampagneRepository()
        self._facturation = StatsFacturationRepository()
        self._paiements = StatsPaiementsRepository()

    # --- Lectures (côté Query) --------------------------------------------- #

    def get_dashboard(self) -> Dashboard:
        """Retourne les stats de la campagne la plus récemment active."""
        campagne = self._campagne.get_derniere()
        if campagne is None:
            return Dashboard(campagne=None, facturation=None, paiements=None)
        return Dashboard(
            campagne=campagne,
            facturation=self._facturation.get_or_none(str(campagne.campagne_id)),
            paiements=self._paiements.get_or_none(str(campagne.campagne_id)),
        )

    def get_stats_campagne(self, campagne_id: str) -> StatsCampagne:
        """Lève ObjectDoesNotExist si la campagne n'a pas de stats."""
        return self._campagne.get(campagne_id)

    def get_stats_globales(self) -> StatsGlobales:
        campagnes = self._campagne.list_all()
        conso = sum((c.consommation_totale for c in campagnes), Decimal("0"))
        montant_facture = sum(
            (f.montant_total_facture for f in self._facturation.list_all()),
            Decimal("0"),
        )
        montant_encaisse = sum(
            (p.montant_encaisse for p in self._paiements.list_all()), Decimal("0")
        )
        return StatsGlobales(
            historique_campagnes=campagnes,
            consommation_totale_globale=conso,
            montant_total_facture_global=montant_facture,
            montant_total_encaisse_global=montant_encaisse,
        )

    # --- Mises à jour (déclenchées par événements) ------------------------- #

    def update_stats_campagne(
        self,
        campagne_id: str,
        nom_campagne: str,
        total_abonnes: int,
        nb_releves: int,
        consommation_totale: float,
    ) -> StatsCampagne:
        stats = self._campagne.get_or_create(campagne_id)
        if nom_campagne:
            stats.nom_campagne = nom_campagne
        stats.total_abonnes = total_abonnes
        stats.nb_releves = nb_releves
        stats.nb_en_attente = max(0, total_abonnes - nb_releves)
        stats.consommation_totale = _dec(consommation_totale)
        stats.pourcentage_progression = (
            (Decimal(nb_releves) / Decimal(total_abonnes) * 100).quantize(_CENT)
            if total_abonnes > 0
            else Decimal("0")
        )
        return self._campagne.save(stats)

    def update_stats_facturation(
        self,
        campagne_id: str,
        delta_factures: int,
        delta_montant: float,
        type_update: str,
    ) -> StatsFacturation:
        stats = self._facturation.get_or_create(campagne_id)
        if type_update == "GENEREE":
            stats.total_factures += delta_factures
            stats.montant_total_facture += _dec(delta_montant)
            # Une facture nouvellement générée est impayée par défaut.
            stats.nb_factures_impayees = max(
                0, stats.nb_factures_impayees + delta_factures
            )
        elif type_update == "ENVOYEE":
            stats.nb_factures_envoyees += delta_factures
        elif type_update == "PAYEE":
            stats.nb_factures_payees += delta_factures
            stats.nb_factures_impayees = max(
                0, stats.nb_factures_impayees - delta_factures
            )
        return self._facturation.save(stats)

    def update_stats_paiements(
        self,
        campagne_id: str,
        montant_paiement: float,
        type_update: str,
    ) -> StatsPaiements:
        stats = self._paiements.get_or_create(campagne_id)
        if type_update == "PAIEMENT":
            stats.montant_encaisse += _dec(montant_paiement)
        # Dérivés recalculés à partir des stats de facturation (source de vérité
        # du montant total facturé et du nombre d'impayés).
        facturation = self._facturation.get_or_none(campagne_id)
        total_facture = (
            facturation.montant_total_facture if facturation else Decimal("0")
        )
        stats.montant_impaye = max(Decimal("0"), total_facture - stats.montant_encaisse)
        if facturation is not None:
            stats.nb_impayes = facturation.nb_factures_impayees
        stats.taux_recouvrement = (
            (stats.montant_encaisse / total_facture * 100).quantize(_CENT)
            if total_facture > 0
            else Decimal("0")
        )
        return self._paiements.save(stats)
