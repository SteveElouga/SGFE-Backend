"""Logique métier du Reporting Service — AgregateurDashboard (ADR-019).

Le côté Query du CQRS : les RPC Update* (déclenchées par les événements des
autres services) mettent à jour les tables dénormalisées ; les lectures
(dashboard, stats) ne font qu'agréger l'existant. Les mises à jour sont
idempotentes autant que possible (upsert par campagne_id).
"""

import logging
from dataclasses import dataclass
from decimal import Decimal

from stats.models import StatsCampagne, StatsFacturation, StatsPaiements
from stats.repositories import (
    StatsCampagneRepository,
    StatsFacturationRepository,
    StatsPaiementsRepository,
)

logger = logging.getLogger(__name__)

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

    def get_stats_completes(self, campagne_id: str) -> Dashboard:
        """Retourne les stats des 3 domaines pour une campagne précise.

        Contrairement à get_stats_campagne, ne lève pas si la campagne est
        inconnue : renvoie un Dashboard aux sous-blocs None (dégradation propre,
        utilisé par la synthèse PDF de l'écran 13).
        """
        return Dashboard(
            campagne=self._campagne.get_or_none(campagne_id),
            facturation=self._facturation.get_or_none(campagne_id),
            paiements=self._paiements.get_or_none(campagne_id),
        )

    def get_stats_globales(self) -> StatsGlobales:
        campagnes = self._campagne.list_all()
        conso = sum((c.consommation_totale for c in campagnes), Decimal("0"))
        montant_facture = sum(
            (f.montant_total_facture for f in self._facturation.list_all()),
            Decimal("0"),
        )
        montant_encaisse = sum((p.montant_encaisse for p in self._paiements.list_all()), Decimal("0"))
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
            (Decimal(nb_releves) / Decimal(total_abonnes) * 100).quantize(_CENT) if total_abonnes > 0 else Decimal("0")
        )
        return self._campagne.save(stats)

    def update_stats_facturation(
        self,
        campagne_id: str,
        delta_factures: int,
        delta_montant: float,
        type_update: str,
        etait_payee: bool = False,
    ) -> StatsFacturation:
        stats = self._facturation.get_or_create(campagne_id)
        if type_update == "GENEREE":
            stats.total_factures += delta_factures
            stats.montant_total_facture += _dec(delta_montant)
            # Une facture nouvellement générée est impayée par défaut.
            stats.nb_factures_impayees = max(0, stats.nb_factures_impayees + delta_factures)
        elif type_update == "ENVOYEE":
            stats.nb_factures_envoyees += delta_factures
        elif type_update == "PAYEE":
            stats.nb_factures_payees += delta_factures
            stats.nb_factures_impayees = max(0, stats.nb_factures_impayees - delta_factures)
        elif type_update == "ANNULEE":
            # Une facture annulée n'a jamais existé pour le lecteur des stats :
            # sans ce retrait, une régularisation (annulation + facture
            # corrigée) comptait les deux à la fois dans le montant facturé.
            # `nb_factures_envoyees` n'est volontairement pas touché : qu'un
            # message soit parti est un fait du passé, indépendant de
            # l'annulation qui a suivi.
            stats.total_factures = max(0, stats.total_factures - delta_factures)
            stats.montant_total_facture = max(Decimal("0"), stats.montant_total_facture - _dec(delta_montant))
            if etait_payee:
                stats.nb_factures_payees = max(0, stats.nb_factures_payees - delta_factures)
            else:
                stats.nb_factures_impayees = max(0, stats.nb_factures_impayees - delta_factures)
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
        elif type_update == "PAIEMENT_ANNULE":
            # Il n'existait aucun chemin de décrément : un versement annulé
            # restait compté dans les recettes, définitivement. Le read model
            # divergeait donc de `statsParMois` (calculé par la gateway, qui
            # exclut bien les paiements annulés) — deux chiffres censés dire la
            # même chose et qui ne s'accordaient pas.
            #
            # Le plancher à zéro n'est pas une correction de fond, c'est un
            # garde-fou : un read model reconstruit à partir d'un flux tronqué
            # peut recevoir une annulation dont il n'a jamais vu le versement.
            stats.montant_encaisse = max(Decimal("0"), stats.montant_encaisse - _dec(montant_paiement))
        # Dérivés recalculés à partir des stats de facturation (source de vérité
        # du montant total facturé et du nombre d'impayés).
        facturation = self._facturation.get_or_none(campagne_id)
        total_facture = facturation.montant_total_facture if facturation else Decimal("0")
        stats.montant_impaye = max(Decimal("0"), total_facture - stats.montant_encaisse)
        if facturation is not None:
            stats.nb_impayes = facturation.nb_factures_impayees
        stats.taux_recouvrement = (
            (stats.montant_encaisse / total_facture * 100).quantize(_CENT) if total_facture > 0 else Decimal("0")
        )
        return self._paiements.save(stats)


# Statuts de facture (facturation_service.proto / factures.models.StatutFacture,
# recopiés en littéraux ici : reporting ne dépend jamais du code d'un autre
# service, seulement de son contrat gRPC).
_STATUT_ANNULEE = "ANNULEE"
_STATUT_PAYEE = "PAYEE"
_STATUT_PARTIELLE = "PARTIELLE"
_STATUT_IMPAYEE = "IMPAYEE"


class ReconciliateurStats:
    """Recalcule StatsFacturation/StatsPaiements depuis les services sources de
    vérité (Facturation, Paiement) — job de réconciliation nocturne.

    StatsCampagne n'a volontairement pas sa place ici : ses mises à jour
    (`update_stats_campagne`) écrivent des valeurs absolues, pas des deltas —
    la prochaine saisie de relevé la recalcule intégralement et corrige donc
    d'elle-même un événement CAMPAGNE_STATS manqué. StatsFacturation et
    StatsPaiements, en revanche, s'incrémentent par delta (`+=`) : un événement
    FACTURATION_STATS ou PAIEMENT_STATS jamais publié (le service producteur a
    planté avant le XADD, par exemple) y laisse une dérive **permanente** —
    contrairement à un événement publié puis perdu entre l'application et le
    XACK, déjà couvert par l'idempotence de `ProcessedEvent` et le rattrapage
    au redémarrage du consumer (`event_consumer.py`). Seule une relecture
    complète des services sources peut corriger cette dérive-là.

    Le périmètre de réconciliation est l'ensemble des campagnes déjà connues
    de StatsCampagne : c'est la liste des campagnes que ce Reporting Service a
    vues passer, indépendamment de tout événement de facturation/paiement.
    """

    def __init__(self, facturation_client=None, paiement_client=None) -> None:
        from stats.grpc_clients import FacturationServiceClient, PaiementServiceClient

        self._facturation_client = facturation_client or FacturationServiceClient()
        self._paiement_client = paiement_client or PaiementServiceClient()
        self._campagne_repo = StatsCampagneRepository()
        self._facturation_repo = StatsFacturationRepository()
        self._paiements_repo = StatsPaiementsRepository()

    def reconcilier_toutes_campagnes(self) -> tuple[int, int]:
        """Réconcilie chaque campagne connue. Retourne (nb_ok, nb_echecs).

        Un échec sur une campagne (service source injoignable) ne doit jamais
        empêcher la réconciliation des autres — chacune est traitée
        indépendamment, ses stats existantes restant inchangées en cas d'échec.
        """
        nb_ok = 0
        nb_echecs = 0
        for campagne_id in [str(c.campagne_id) for c in self._campagne_repo.list_all()]:
            try:
                self.reconcilier_campagne(campagne_id)
                nb_ok += 1
            except Exception:
                logger.exception(
                    "Réconciliation échouée pour la campagne %s — stats existantes conservées telles quelles.",
                    campagne_id,
                )
                nb_echecs += 1
        return nb_ok, nb_echecs

    def reconcilier_campagne(self, campagne_id: str) -> None:
        """Recalcule StatsFacturation/StatsPaiements d'UNE campagne depuis les sources de vérité.

        Lève si Facturation ou Paiement Service est inaccessible (voir
        `grpc_clients.py`) — l'appelant décide alors de conserver les stats
        existantes plutôt que de les écraser par des zéros trompeurs.

        `nb_factures_envoyees` n'est délibérément jamais touché : ce compteur
        provient de l'envoi WhatsApp (Notification Service), un fait qui n'a
        pas de trace durable côté Facturation Service — rien à réconcilier
        depuis cette source, il reste tel que les événements l'ont construit.
        """
        factures = self._facturation_client.list_factures_par_campagne(campagne_id)
        paiements = self._paiement_client.list_paiements_par_campagne(campagne_id)

        actives = [f for f in factures if f["statut"] != _STATUT_ANNULEE]
        montant_total_facture = sum((_dec(f["montant"]) for f in actives), Decimal("0"))
        nb_impayees = sum(1 for f in actives if f["statut"] == _STATUT_IMPAYEE)

        # Rien à réconcilier et rien de préexistant : ne pas fabriquer une
        # ligne de stats à zéro pour une campagne qui n'a simplement pas encore
        # été facturée (ex. toujours EN_COURS) — `facturation=None` au dashboard
        # a un sens différent de `facturation=stats à zéro`.
        facturation_existante = self._facturation_repo.get_or_none(campagne_id)
        if facturation_existante is None and not actives:
            return

        stats_facturation = facturation_existante or self._facturation_repo.get_or_create(campagne_id)
        stats_facturation.total_factures = len(actives)
        stats_facturation.montant_total_facture = montant_total_facture
        stats_facturation.nb_factures_payees = sum(1 for f in actives if f["statut"] == _STATUT_PAYEE)
        stats_facturation.nb_factures_partielles = sum(1 for f in actives if f["statut"] == _STATUT_PARTIELLE)
        stats_facturation.nb_factures_impayees = nb_impayees
        self._facturation_repo.save(stats_facturation)

        paiements_non_annules = [p for p in paiements if not p["annule"]]
        paiements_existants = self._paiements_repo.get_or_none(campagne_id)
        if paiements_existants is None and not paiements_non_annules and not actives:
            return

        montant_encaisse = sum((_dec(p["montant"]) for p in paiements_non_annules), Decimal("0"))
        stats_paiements = paiements_existants or self._paiements_repo.get_or_create(campagne_id)
        stats_paiements.montant_encaisse = montant_encaisse
        stats_paiements.montant_impaye = max(Decimal("0"), montant_total_facture - montant_encaisse)
        stats_paiements.nb_impayes = nb_impayees
        stats_paiements.taux_recouvrement = (
            (montant_encaisse / montant_total_facture * 100).quantize(_CENT)
            if montant_total_facture > 0
            else Decimal("0")
        )
        self._paiements_repo.save(stats_paiements)
