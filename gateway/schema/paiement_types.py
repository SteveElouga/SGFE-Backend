"""Types Strawberry pour le Paiement Service."""

import strawberry


@strawberry.type
class SoldeFacture:
    facture_id: str
    montant_total: float
    montant_paye: float
    solde_restant: float
    statut: str


@strawberry.type
class Paiement:
    paiement_id: str
    facture_id: str
    montant: float
    date_paiement: str
    mode_paiement: str
    reference_transaction: str
    created_at: str
    # Nom d'utilisateur (Auth Service) de l'opérateur ayant enregistré le
    # paiement — résolu depuis enregistre_par (user_id) par l'appelant, voir
    # paiement_queries.py/paiement_mutations.py. Vide si non résolu.
    operateur: str = ""


@strawberry.type
class SuiviImpaye:
    suivi_id: str
    facture_id: str
    abonne_id: str
    date_depassement: str
    etape_actuelle: int
    resolu_le: str


def solde_from_grpc(r) -> SoldeFacture:
    return SoldeFacture(
        facture_id=r.facture_id,
        montant_total=r.montant_total,
        montant_paye=r.montant_paye,
        solde_restant=r.solde_restant,
        statut=r.statut,
    )


def paiement_from_grpc(r, operateur: str = "") -> Paiement:
    return Paiement(
        paiement_id=r.paiement_id,
        facture_id=r.facture_id,
        montant=r.montant,
        date_paiement=r.date_paiement,
        mode_paiement=r.mode_paiement,
        reference_transaction=r.reference_transaction,
        created_at=r.created_at,
        operateur=operateur,
    )


def suivi_from_grpc(r) -> SuiviImpaye:
    return SuiviImpaye(
        suivi_id=r.suivi_id,
        facture_id=r.facture_id,
        abonne_id=r.abonne_id,
        date_depassement=r.date_depassement,
        etape_actuelle=r.etape_actuelle,
        resolu_le=r.resolu_le,
    )
