"""Types Strawberry pour le Reporting Service (tableau de bord, docs/ARCHITECTURE.md)."""

import strawberry


@strawberry.type
class StatsCampagne:
    campagne_id: strawberry.ID
    nom_campagne: str
    total_abonnes: int
    nb_releves: int
    nb_en_attente: int
    pourcentage_progression: float
    consommation_totale: float


@strawberry.type
class StatsFacturation:
    total_factures: int
    montant_total_facture: float
    nb_factures_envoyees: int
    nb_factures_payees: int
    nb_impayes: int


@strawberry.type
class StatsPaiements:
    montant_encaisse: float
    montant_impaye: float
    nb_impayes: int
    taux_recouvrement: float


@strawberry.type
class Dashboard:
    """Snapshot du tableau de bord — les sous-blocs sont nuls si aucune donnée."""

    campagne_en_cours: StatsCampagne | None
    facturation_en_cours: StatsFacturation | None
    paiements_en_cours: StatsPaiements | None


@strawberry.type
class StatsGlobales:
    historique_campagnes: list[StatsCampagne]
    consommation_totale_globale: float
    montant_total_facture_global: float
    montant_total_encaisse_global: float


def stats_campagne_from_grpc(r) -> StatsCampagne:
    return StatsCampagne(
        campagne_id=strawberry.ID(r.campagne_id),
        nom_campagne=r.nom_campagne,
        total_abonnes=r.total_abonnes,
        nb_releves=r.nb_releves,
        nb_en_attente=r.nb_en_attente,
        pourcentage_progression=r.pourcentage_progression,
        consommation_totale=r.consommation_totale,
    )


def stats_facturation_from_grpc(r) -> StatsFacturation:
    return StatsFacturation(
        total_factures=r.total_factures,
        montant_total_facture=r.montant_total_facture,
        nb_factures_envoyees=r.nb_factures_envoyees,
        nb_factures_payees=r.nb_factures_payees,
        nb_impayes=r.nb_impayes,
    )


def stats_paiements_from_grpc(r) -> StatsPaiements:
    return StatsPaiements(
        montant_encaisse=r.montant_encaisse,
        montant_impaye=r.montant_impaye,
        nb_impayes=r.nb_impayes,
        taux_recouvrement=r.taux_recouvrement,
    )


def dashboard_from_grpc(r) -> Dashboard:
    # Un campagne_id vide signale l'absence de données (aucune campagne agrégée).
    return Dashboard(
        campagne_en_cours=(stats_campagne_from_grpc(r.campagne_en_cours) if r.campagne_en_cours.campagne_id else None),
        facturation_en_cours=(
            stats_facturation_from_grpc(r.facturation_en_cours) if r.facturation_en_cours.campagne_id else None
        ),
        paiements_en_cours=(
            stats_paiements_from_grpc(r.paiements_en_cours) if r.paiements_en_cours.campagne_id else None
        ),
    )


def stats_globales_from_grpc(r) -> StatsGlobales:
    return StatsGlobales(
        historique_campagnes=[stats_campagne_from_grpc(c) for c in r.historique_campagnes],
        consommation_totale_globale=r.consommation_totale_globale,
        montant_total_facture_global=r.montant_total_facture_global,
        montant_total_encaisse_global=r.montant_total_encaisse_global,
    )
