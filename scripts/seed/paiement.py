"""Seed de démo — soldes + paiements. Idempotent.

- Chaque facture démo reçoit son SoldeFacture (porte le campagne_id → c'est la
  seule jointure paiement↔campagne, indispensable pour list_paiements_par_campagne).
- DISSOCIATION : pay-1 encaisse ce mois-ci (M0) une facture générée il y a 2 mois (M2).
- pay-4 est ANNULÉ → doit être exclu de l'encaissé par statsParMois.
"""

import uuid
from datetime import date, datetime, timedelta, timezone
from decimal import Decimal

from paiements.models import Paiement, SoldeFacture

NS = uuid.uuid5(uuid.NAMESPACE_DNS, "sgfe-demo-seed")
U_ADMIN = str(uuid.uuid5(NS, "user-admin"))
C_ALPHA = str(uuid.uuid5(NS, "camp-alpha"))
C_BETA = str(uuid.uuid5(NS, "camp-beta"))
C_GAMMA = str(uuid.uuid5(NS, "camp-gamma"))

# Retard simulé (en jours) des soldes IMPAYES de démo — voir date_limite() ci-dessous.
JOURS_RETARD_IMPAYE = 10


def mois_decale(d: date, k: int) -> tuple[int, int]:
    total = d.year * 12 + (d.month - 1) - k
    return total // 12, total % 12 + 1


def le_15(annee: int, mois: int) -> date:
    return date(annee, mois, 15)


def date_limite(statut: str, annee: int, mois: int) -> date:
    """Date limite de paiement d'un solde de démo.

    BUG CORRIGÉ (repéré depuis SGFE-frontend#169, paiement-encaissement.spec.ts) :
    `date_limite_paiement` était fixée pour TOUS les soldes à `le_15(A0, M0)`
    (le 15 du mois COURANT), y compris pour les soldes IMPAYES. Or
    `list_impayes()` (services/paiement/paiements/repositories.py) filtre sur
    `date_limite_paiement__lt=date.today()` : avant le 16 de chaque mois,
    AUCUN solde ne matchait jamais ce filtre — `/impayes` restait vide et le
    bouton « + Paiement » que ce spec attend n'apparaissait jamais. Un bug
    dépendant du calendrier d'exécution du seed, pas un vrai signal métier.

    Un solde IMPAYE de démo reçoit désormais TOUJOURS une date limite dans le
    passé par rapport à `date.today()` (`date.today() - JOURS_RETARD_IMPAYE`
    jours), quel que soit le jour d'exécution du script. Les soldes déjà
    PAYES gardent la date de facturation « normale » (le 15 du mois de la
    campagne) : `list_impayes()` les exclut de toute façon par leur statut,
    donc cette date n'a jamais d'incidence sur le bug ci-dessus pour eux.
    """
    if statut == "IMPAYEE":
        return date.today() - timedelta(days=JOURS_RETARD_IMPAYE)
    return le_15(annee, mois)


def fid(cle: str) -> str:
    return str(uuid.uuid5(NS, cle))


def abid(cle: str) -> str:
    return str(uuid.uuid5(NS, f"abonne-{cle}"))


today = date.today()
A2, M2 = mois_decale(today, 2)
A1, M1 = mois_decale(today, 1)
A0, M0 = mois_decale(today, 0)

# (clé_facture, campagne_id, montant_total, montant_paye, statut)
SOLDES = [
    ("fact-a1", C_ALPHA, "12000", "12000", "PAYEE"),
    ("fact-a2", C_ALPHA, "8000", "0", "IMPAYEE"),
    ("fact-b1", C_BETA, "15000", "15000", "PAYEE"),
    ("fact-c1", C_GAMMA, "20000", "20000", "PAYEE"),
    ("fact-c2", C_GAMMA, "5000", "0", "IMPAYEE"),
]
for cle, campagne_id, total, paye, statut in SOLDES:
    total_d, paye_d = Decimal(total), Decimal(paye)
    SoldeFacture.objects.update_or_create(
        facture_id=fid(cle),
        defaults={
            "abonne_id": abid(cle),
            "campagne_id": campagne_id,
            "montant_total": total_d,
            "montant_paye": paye_d,
            "solde_restant": total_d - paye_d,
            "statut": statut,
            "date_limite_paiement": date_limite(statut, A0, M0),
        },
    )

# (clé_paiement, clé_facture, montant, annee, mois, mode, annulé)
PAIEMENTS = [
    (
        "pay-1",
        "fact-a1",
        "12000",
        A0,
        M0,
        "MOBILE_MONEY",
        False,
    ),  # dissociation : payé en M0, facture de M2
    ("pay-2", "fact-b1", "15000", A1, M1, "ESPECES", False),
    ("pay-3", "fact-c1", "20000", A0, M0, "MOBILE_MONEY", False),
    (
        "pay-4",
        "fact-c2",
        "5000",
        A0,
        M0,
        "ESPECES",
        True,
    ),  # ANNULÉ → exclu de l'encaissé
]
for cle, fcle, montant, annee, mois, mode, annule in PAIEMENTS:
    pid = uuid.uuid5(NS, cle)
    defaults = {
        "facture_id": fid(fcle),
        "abonne_id": abid(fcle),
        "montant": Decimal(montant),
        "date_paiement": le_15(annee, mois),
        "mode_paiement": mode,
        "reference_transaction": cle.upper() if mode == "MOBILE_MONEY" else "",
        "enregistre_par": U_ADMIN,
        "annule": annule,
        "annule_le": datetime(A0, M0, 16, 12, 0, tzinfo=timezone.utc)
        if annule
        else None,
        "annule_par": U_ADMIN if annule else "",
        "motif_annulation": "Démo — annulé (doit être exclu de l'encaissé)"
        if annule
        else "",
    }
    Paiement.objects.update_or_create(id=pid, defaults=defaults)
    print(
        f"  {cle:6} facture={fcle:8} {annee}-{mois:02d} {montant:>6} {mode}{' [ANNULÉ]' if annule else ''}"
    )

print(f"OK — {len(SOLDES)} soldes, {len(PAIEMENTS)} paiements de démo (dont 1 annulé)")
