"""Types Strawberry pour le Reporting Service (tableau de bord, docs/ARCHITECTURE.md)."""

from datetime import date
from typing import Any

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


def stats_campagne_from_grpc(r: Any) -> StatsCampagne:
    return StatsCampagne(
        campagne_id=strawberry.ID(r.campagne_id),
        nom_campagne=r.nom_campagne,
        total_abonnes=r.total_abonnes,
        nb_releves=r.nb_releves,
        nb_en_attente=r.nb_en_attente,
        pourcentage_progression=r.pourcentage_progression,
        consommation_totale=r.consommation_totale,
    )


def stats_facturation_from_grpc(r: Any) -> StatsFacturation:
    return StatsFacturation(
        total_factures=r.total_factures,
        montant_total_facture=r.montant_total_facture,
        nb_factures_envoyees=r.nb_factures_envoyees,
        nb_factures_payees=r.nb_factures_payees,
        nb_impayes=r.nb_impayes,
    )


def stats_paiements_from_grpc(r: Any) -> StatsPaiements:
    return StatsPaiements(
        montant_encaisse=r.montant_encaisse,
        montant_impaye=r.montant_impaye,
        nb_impayes=r.nb_impayes,
        taux_recouvrement=r.taux_recouvrement,
    )


def dashboard_from_grpc(r: Any) -> Dashboard:
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


def stats_globales_from_grpc(r: Any) -> StatsGlobales:
    return StatsGlobales(
        historique_campagnes=[stats_campagne_from_grpc(c) for c in r.historique_campagnes],
        consommation_totale_globale=r.consommation_totale_globale,
        montant_total_facture_global=r.montant_total_facture_global,
        montant_total_encaisse_global=r.montant_total_encaisse_global,
    )


# ── statsParMois : agrégat mensuel réel ───────────────────────────────────────
# Calculé par fan-out dans la gateway (le Reporting Service ne porte aucune
# dimension temporelle). Dissocie le mois de PAIEMENT (encaissé) du mois de
# GÉNÉRATION (facturé) — un paiement de juillet sur une facture de mai compte
# en juillet pour l'encaissé et en mai pour le facturé.


@strawberry.type
class StatMois:
    """Agrégat d'un mois. Une ligne par mois de la fenêtre glissante — un mois
    sans donnée reste présent avec des zéros (le frontend calcule des deltas
    honnêtes : « juin = 0 » ≠ « juin manquant »)."""

    mois: str  # "AAAA-MM" (tri lexicographique = chronologique)
    annee: int
    mois_num: int
    encaisse: int  # SUM(Paiement.montant) du mois de date_paiement, annulés exclus
    facture: int  # SUM(Facture.montant) du mois de date_generation
    consommation: int  # SUM(Facture.consommation) du mois de génération (fallback ticket)
    nb_paiements: int
    nb_factures: int


def _fenetre_mois(nb_mois: int, today: date) -> list[tuple[int, int]]:
    """Les `nb_mois` derniers mois (annee, mois_num), du plus récent au plus ancien."""
    mois: list[tuple[int, int]] = []
    annee, m = today.year, today.month
    for _ in range(nb_mois):
        mois.append((annee, m))
        m -= 1
        if m == 0:
            m, annee = 12, annee - 1
    return mois


def build_stats_par_mois(factures: Any, paiements: Any, nb_mois: int, today: date | None = None) -> list["StatMois"]:
    """Bucketise factures/paiements par mois et renvoie `nb_mois` lignes triées du
    plus récent ([0] = mois courant) au plus ancien, zéros compris.

    - encaisse : mois de `date_paiement` (paiements `annule` exclus) ;
    - facture / consommation : mois de `date_generation`.
    Les dates traversent le gRPC en chaînes ; le mois = 7 premiers caractères
    ("AAAA-MM"), valable pour `date_paiement` ("AAAA-MM-JJ") comme pour
    `date_generation` (datetime ISO).
    """
    today = today or date.today()
    enc: dict[str, float] = {}
    fac: dict[str, float] = {}
    con: dict[str, float] = {}
    nb_p: dict[str, int] = {}
    nb_f: dict[str, int] = {}

    for p in paiements:
        if getattr(p, "annule", False):
            continue
        cle = (p.date_paiement or "")[:7]
        if len(cle) != 7:
            continue
        enc[cle] = enc.get(cle, 0.0) + p.montant
        nb_p[cle] = nb_p.get(cle, 0) + 1

    for f in factures:
        cle = (f.date_generation or "")[:7]
        if len(cle) != 7:
            continue
        fac[cle] = fac.get(cle, 0.0) + f.montant
        con[cle] = con.get(cle, 0.0) + f.consommation
        nb_f[cle] = nb_f.get(cle, 0) + 1

    resultat: list[StatMois] = []
    for annee, mois_num in _fenetre_mois(nb_mois, today):
        cle = f"{annee:04d}-{mois_num:02d}"
        resultat.append(
            StatMois(
                mois=cle,
                annee=annee,
                mois_num=mois_num,
                encaisse=round(enc.get(cle, 0.0)),
                facture=round(fac.get(cle, 0.0)),
                consommation=round(con.get(cle, 0.0)),
                nb_paiements=nb_p.get(cle, 0),
                nb_factures=nb_f.get(cle, 0),
            )
        )
    return resultat
