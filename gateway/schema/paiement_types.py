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
    # Statut résultant de la facture (IMPAYEE/PARTIELLE/PAYEE) au moment du
    # paiement. Renseigné uniquement par la souscription paiementCree (l'événement
    # Redis auto-porteur le transporte) ; vide via les queries gRPC classiques.
    statut_facture: str = ""
    # Annulation (traçabilité) — annule=true si le paiement a été annulé.
    annule: bool = False
    annule_le: str = ""
    annule_par: str = ""
    motif_annulation: str = ""


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
        annule=r.annule,
        annule_le=r.annule_le,
        annule_par=r.annule_par,
        motif_annulation=r.motif_annulation,
    )


def paiement_from_event(data: dict, operateur: str = "") -> Paiement:
    """Construit un Paiement depuis l'événement Redis auto-porteur (paiementCree).

    Le service paiement n'expose pas de GetPaiement : l'événement transporte
    directement tous les champs affichés (voir paiements/event_publisher.py).
    """
    return Paiement(
        paiement_id=data.get("paiement_id", ""),
        facture_id=data.get("facture_id", ""),
        montant=float(data.get("montant") or 0),
        date_paiement=data.get("date_paiement", "") or "",
        mode_paiement=data.get("mode_paiement", "") or "",
        reference_transaction=data.get("reference_transaction", "") or "",
        created_at=data.get("created_at", "") or "",
        operateur=operateur,
        statut_facture=data.get("statut_facture", "") or "",
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
