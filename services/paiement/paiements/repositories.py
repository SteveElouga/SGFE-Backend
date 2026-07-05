"""Accès base de données du Paiement Service."""

from datetime import date

from django.core.exceptions import ObjectDoesNotExist

from .models import Paiement, SoldeFacture, StatutSolde, SuiviImpaye


class PaiementRepository:
    """Accès base de données pour les paiements."""

    def create(
        self,
        facture_id: str,
        abonne_id: str,
        montant: object,
        date_paiement: date,
        mode_paiement: str,
        reference_transaction: str,
        enregistre_par: str,
    ) -> Paiement:
        """Crée un nouveau paiement en base."""
        return Paiement.objects.create(
            facture_id=facture_id,
            abonne_id=abonne_id,
            montant=montant,
            date_paiement=date_paiement,
            mode_paiement=mode_paiement,
            reference_transaction=reference_transaction,
            enregistre_par=enregistre_par,
        )

    def list_by_facture_and_abonne(self, facture_id: str, abonne_id: str) -> list[Paiement]:
        """Liste les paiements filtrés par facture et/ou abonné."""
        qs = Paiement.objects.all()
        if facture_id:
            qs = qs.filter(facture_id=facture_id)
        if abonne_id:
            qs = qs.filter(abonne_id=abonne_id)
        return list(qs)

    def list_by_campagne(self, campagne_id: str) -> list[Paiement]:
        """Liste les paiements des factures rattachées à une campagne.

        Le modèle Paiement ne porte pas la campagne : le rattachement passe par
        SoldeFacture (qui porte `campagne_id` depuis l'initialisation du solde).
        On récupère donc les factures de la campagne puis leurs paiements,
        triés du plus ancien au plus récent (ordre naturel pour un export).
        """
        facture_ids = SoldeFacture.objects.filter(campagne_id=campagne_id).values_list("facture_id", flat=True)
        return list(Paiement.objects.filter(facture_id__in=facture_ids).order_by("date_paiement", "created_at"))


class SoldeFactureRepository:
    """Accès base de données pour les soldes de factures."""

    def create(
        self,
        facture_id: str,
        abonne_id: str,
        montant_total: object,
        date_limite_paiement: date,
        campagne_id: str = "",
    ) -> SoldeFacture:
        """Initialise le solde d'une facture (statut IMPAYEE, montant_paye=0)."""
        return SoldeFacture.objects.create(
            facture_id=facture_id,
            abonne_id=abonne_id,
            campagne_id=campagne_id,
            montant_total=montant_total,
            montant_paye=0,
            solde_restant=montant_total,
            statut=StatutSolde.IMPAYEE,
            date_limite_paiement=date_limite_paiement,
        )

    def get_by_facture_id(self, facture_id: str, for_update: bool = False) -> SoldeFacture:
        """Récupère le solde d'une facture — lève ObjectDoesNotExist si introuvable.

        Si `for_update=True`, verrouille la ligne (`SELECT ... FOR UPDATE`) afin
        de sérialiser les versements concurrents sur une même facture ; doit
        alors être appelé dans une transaction (`transaction.atomic`).
        """
        qs = SoldeFacture.objects.all()
        if for_update:
            qs = qs.select_for_update()
        try:
            return qs.get(pk=facture_id)
        except SoldeFacture.DoesNotExist:
            raise ObjectDoesNotExist(f"Solde introuvable pour la facture : {facture_id}")

    def update_after_paiement(self, solde: SoldeFacture, montant_verse: object) -> SoldeFacture:
        """Met à jour le solde après un versement et recalcule le statut."""
        from decimal import Decimal

        montant_verse_d = Decimal(str(montant_verse))
        solde.montant_paye = Decimal(str(solde.montant_paye)) + montant_verse_d
        solde.solde_restant = Decimal(str(solde.montant_total)) - solde.montant_paye

        # Calcul du statut selon les règles métier
        if solde.montant_paye <= 0:
            solde.statut = StatutSolde.IMPAYEE
        elif solde.montant_paye >= Decimal(str(solde.montant_total)):
            solde.statut = StatutSolde.PAYEE
        else:
            solde.statut = StatutSolde.PARTIELLE

        solde.save(update_fields=["montant_paye", "solde_restant", "statut", "updated_at"])
        return solde

    def list_impayes(self) -> list[SoldeFacture]:
        """Retourne toutes les factures dont la date limite est dépassée et non payées."""
        return list(
            SoldeFacture.objects.filter(
                date_limite_paiement__lt=date.today(),
            ).exclude(statut=StatutSolde.PAYEE)
        )


class SuiviImpayeRepository:
    """Accès base de données pour le suivi des impayés."""

    def get_or_create(self, facture_id: str, abonne_id: str, date_depassement: date) -> tuple[SuiviImpaye, bool]:
        """Retourne le suivi existant ou en crée un nouveau."""
        return SuiviImpaye.objects.get_or_create(
            facture_id=facture_id,
            defaults={
                "abonne_id": abonne_id,
                "date_depassement": date_depassement,
                "etape_actuelle": 1,
            },
        )

    def get_by_facture_id(self, facture_id: str) -> SuiviImpaye:
        """Récupère le suivi d'une facture — lève ObjectDoesNotExist si introuvable."""
        try:
            return SuiviImpaye.objects.get(facture_id=facture_id)
        except SuiviImpaye.DoesNotExist:
            raise ObjectDoesNotExist(f"Suivi impayé introuvable pour la facture : {facture_id}")

    def save_suivi(self, suivi: SuiviImpaye) -> SuiviImpaye:
        """Persiste les modifications d'un suivi."""
        suivi.save()
        return suivi
