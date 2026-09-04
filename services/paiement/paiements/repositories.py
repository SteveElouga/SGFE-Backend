"""Accès base de données du Paiement Service."""

import uuid
from datetime import date, datetime
from decimal import Decimal

from django.core.exceptions import ObjectDoesNotExist
from django.db.models import QuerySet, Sum

from .models import (
    AvoirAbonne,
    MouvementAvoir,
    Paiement,
    SessionPaiementEnLigne,
    SoldeFacture,
    StatutSolde,
    SuiviImpaye,
)


class PaiementRepository:
    """Accès base de données pour les paiements."""

    def create(
        self,
        facture_id: str,
        abonne_id: str,
        montant: Decimal,
        date_paiement: date,
        mode_paiement: str,
        reference_transaction: str,
        enregistre_par: str,
        montant_excedent: Decimal = Decimal("0"),
        versement_id: "uuid.UUID | str | None" = None,
    ) -> Paiement:
        """Crée une écriture de paiement.

        `montant` est la part imputée à CETTE facture, pas la somme reçue : un
        versement peut se répartir sur plusieurs factures.

        `montant_excedent` est la part du versement partie à l'avoir. Elle ne se
        pose que sur la dernière écriture — c'est elle qu'on annulera pour
        reprendre le crédit.

        `versement_id` regroupe les écritures d'un même versement. Omis, une
        écriture forme son propre versement (cas d'une imputation isolée, comme
        celle d'un avoir à la création d'une facture).
        """
        champs = {
            "facture_id": facture_id,
            "abonne_id": abonne_id,
            "montant": montant,
            "montant_excedent": montant_excedent,
            "date_paiement": date_paiement,
            "mode_paiement": mode_paiement,
            "reference_transaction": reference_transaction,
            "enregistre_par": enregistre_par,
        }
        if versement_id is not None:
            champs["versement_id"] = versement_id
        return Paiement.objects.create(**champs)

    def list_du_versement(self, versement_id: "uuid.UUID | str") -> list[Paiement]:
        """Toutes les écritures nées d'un même versement, dans l'ordre d'imputation.

        Un versement s'impute sur la facture visée puis sur les impayés : il
        produit donc plusieurs écritures. L'annulation les charge toutes — on
        annule un versement, pas une de ses lignes.
        """
        return list(Paiement.objects.filter(versement_id=versement_id).order_by("created_at"))

    def dernier_du_versement(self, versement_id: "uuid.UUID | str") -> Paiement | None:
        """Dernière écriture d'un versement — celle qui porte l'excédent."""
        return Paiement.objects.filter(versement_id=versement_id).order_by("-created_at").first()

    def get_by_reference(self, reference_transaction: str) -> Paiement | None:
        """Paiement portant cette référence de transaction, ou None — support de
        l'idempotence de l'enregistrement (rejeu d'une même transaction)."""
        return Paiement.objects.filter(reference_transaction=reference_transaction).first()

    def get_by_id(self, paiement_id: str) -> Paiement:
        """Paiement par id — lève ObjectDoesNotExist si introuvable."""
        try:
            return Paiement.objects.get(pk=paiement_id)
        except Paiement.DoesNotExist:
            raise ObjectDoesNotExist(f"Paiement introuvable : {paiement_id}")

    def marquer_annule(self, paiement: Paiement, motif: str, annule_par: str) -> Paiement:
        """Annulation douce d'un paiement : le marque annulé + traçabilité."""
        from django.utils import timezone

        paiement.annule = True
        paiement.annule_le = timezone.now()
        paiement.annule_par = annule_par
        paiement.motif_annulation = motif
        paiement.save(update_fields=["annule", "annule_le", "annule_par", "motif_annulation"])
        return paiement

    def _filtres(
        self,
        facture_id: str = "",
        abonne_id: str = "",
        date_debut: date | None = None,
        date_fin: date | None = None,
    ) -> QuerySet[Paiement]:
        """Queryset filtré, partagé par `list_by_facture_and_abonne` et
        `count_by_facture_and_abonne` — le comptage et la page rendue portent
        ainsi toujours sur les mêmes critères, jamais sur la table entière."""
        qs = Paiement.objects.all()
        if facture_id:
            qs = qs.filter(facture_id=facture_id)
        if abonne_id:
            qs = qs.filter(abonne_id=abonne_id)
        if date_debut:
            qs = qs.filter(date_paiement__gte=date_debut)
        if date_fin:
            qs = qs.filter(date_paiement__lte=date_fin)
        return qs

    def list_by_facture_and_abonne(
        self,
        facture_id: str,
        abonne_id: str,
        date_debut: date | None = None,
        date_fin: date | None = None,
        limit: int | None = None,
        offset: int | None = None,
    ) -> list[Paiement]:
        """Liste les paiements filtrés par facture, abonné et/ou PÉRIODE.

        Les bornes portent sur `date_paiement` — la date de caisse, celle qu'un
        journal demande. Incluses.

        Tri chronologique dès qu'une borne est posée, ou qu'une pagination est
        demandée (une page n'a de sens que sur un ordre stable) : un journal se
        lit dans l'ordre où l'argent est entré. Sans borne ni pagination,
        l'ordre reste celui d'avant, pour ne pas changer le comportement des
        appelants existants.

        `limit`/`offset` optionnels, appliqués après le tri : omis (`None`),
        la liste complète filtrée est retournée — comportement historique
        préservé à l'identique.
        """
        qs = self._filtres(facture_id, abonne_id, date_debut, date_fin)
        paginer = limit is not None or offset is not None
        if date_debut or date_fin or paginer:
            qs = qs.order_by("date_paiement", "created_at")
        if paginer:
            start = offset or 0
            qs = qs[start : start + limit] if limit is not None else qs[start:]
        return list(qs)

    def count_by_facture_and_abonne(
        self,
        facture_id: str = "",
        abonne_id: str = "",
        date_debut: date | None = None,
        date_fin: date | None = None,
    ) -> int:
        """Nombre total de paiements correspondant au filtre, indépendamment de
        toute pagination."""
        return self._filtres(facture_id, abonne_id, date_debut, date_fin).count()

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
        montant_total: Decimal,
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

    def get_if_exists(self, facture_id: str) -> "SoldeFacture | None":
        """Solde de la facture, ou None — support de l'initialisation idempotente
        (ré-init / réconciliation d'une facture orpheline)."""
        return SoldeFacture.objects.filter(pk=facture_id).first()

    def update_after_paiement(self, solde: SoldeFacture, montant_verse: Decimal) -> SoldeFacture:
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

    def update_after_annulation(self, solde: SoldeFacture, montant_annule: Decimal) -> SoldeFacture:
        """Rétablit le solde après annulation d'un paiement (retire le montant versé)."""
        from decimal import Decimal

        montant_d = Decimal(str(montant_annule))
        solde.montant_paye = Decimal(str(solde.montant_paye)) - montant_d
        if solde.montant_paye < 0:
            solde.montant_paye = Decimal("0")
        solde.solde_restant = Decimal(str(solde.montant_total)) - solde.montant_paye

        if solde.montant_paye <= 0:
            solde.statut = StatutSolde.IMPAYEE
        elif solde.montant_paye >= Decimal(str(solde.montant_total)):
            solde.statut = StatutSolde.PAYEE
        else:
            solde.statut = StatutSolde.PARTIELLE

        solde.save(update_fields=["montant_paye", "solde_restant", "statut", "updated_at"])
        return solde

    def annuler(self, solde: SoldeFacture) -> SoldeFacture:
        """Éteint un solde parce que sa facture est annulée.

        Le solde restant tombe à zéro sans qu'un versement l'ait éteint : c'est
        exactement pourquoi le statut doit être ANNULEE et non PAYEE. Confondre
        les deux ferait entrer dans les recettes une somme que personne n'a
        versée, et rendrait l'écart introuvable au rapprochement.
        """
        solde.solde_restant = Decimal("0")
        solde.statut = StatutSolde.ANNULEE
        solde.save(update_fields=["solde_restant", "statut", "updated_at"])
        return solde

    def list_non_soldes_par_abonne(self, abonne_id: str, for_update: bool = False) -> list[SoldeFacture]:
        """Soldes non éteints d'un abonné, **du plus ancien au plus récent**.

        L'ordre porte la règle d'imputation : un versement éteint d'abord la
        dette la plus ancienne. Le tri se fait sur la date limite de paiement,
        et non sur la date de création — c'est l'exigibilité qui compte, et une
        régularisation d'arriéré est exigible immédiatement même si elle vient
        d'être saisie.

        `for_update=True` verrouille les lignes pour sérialiser deux versements
        concurrents sur un même abonné : sans cela, deux caissiers pourraient
        imputer le même solde deux fois.
        """
        qs = SoldeFacture.objects.filter(abonne_id=abonne_id).exclude(
            statut__in=(StatutSolde.PAYEE, StatutSolde.ANNULEE)
        )
        if for_update:
            qs = qs.select_for_update()
        return list(qs.order_by("date_limite_paiement", "facture_id"))

    def total_du_abonne(self, abonne_id: str, hors_facture_id: str = "") -> Decimal:
        """Somme des soldes restants d'un abonné, hors une facture donnée.

        `hors_facture_id` sert à l'impression : sur une facture, le « solde
        antérieur » est ce que l'abonné doit **en plus** de celle qu'il tient
        entre les mains.
        """
        qs = SoldeFacture.objects.filter(abonne_id=abonne_id).exclude(
            statut__in=(StatutSolde.PAYEE, StatutSolde.ANNULEE)
        )
        if hors_facture_id:
            qs = qs.exclude(facture_id=hors_facture_id)
        total = qs.aggregate(t=Sum("solde_restant"))["t"]
        return Decimal(str(total)) if total is not None else Decimal("0")

    def list_impayes(self) -> list[SoldeFacture]:
        """Retourne toutes les factures dont la date limite est dépassée et non payées."""
        return list(
            SoldeFacture.objects.filter(
                date_limite_paiement__lt=date.today(),
            ).exclude(statut__in=(StatutSolde.PAYEE, StatutSolde.ANNULEE))
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


class SessionPaiementRepository:
    """Accès base de données pour les sessions de paiement en ligne (mock)."""

    def create(
        self,
        facture_id: str,
        abonne_id: str,
        montant: Decimal,
        token_espace: str,
        expire_a: datetime,
    ) -> SessionPaiementEnLigne:
        """Ouvre une nouvelle session de paiement en ligne, statut EN_ATTENTE."""
        return SessionPaiementEnLigne.objects.create(
            facture_id=facture_id,
            abonne_id=abonne_id,
            montant=montant,
            token_espace=token_espace,
            expire_a=expire_a,
        )

    def get_by_id(self, session_id: str) -> SessionPaiementEnLigne:
        """Session par id — lève ObjectDoesNotExist si introuvable."""
        try:
            return SessionPaiementEnLigne.objects.get(pk=session_id)
        except (SessionPaiementEnLigne.DoesNotExist, ValueError, TypeError):
            # `ValueError`/`TypeError` : un `session_id` mal formé (pas un
            # UUID) lève avant même la requête SQL — traité comme
            # « introuvable », pas comme une erreur serveur.
            raise ObjectDoesNotExist(f"Session de paiement introuvable : {session_id}")

    def marquer_statut(self, session: SessionPaiementEnLigne, statut: str) -> SessionPaiementEnLigne:
        """Change le statut d'une session (transition EN_ATTENTE -> état terminal)."""
        session.statut = statut
        session.save(update_fields=["statut"])
        return session


class AvoirAbonneRepository:
    """Accès base de données pour les avoirs (crédits) des abonnés."""

    def get_if_exists(self, abonne_id: str) -> AvoirAbonne | None:
        """Avoir de l'abonné, ou None s'il n'en a aucun."""
        return AvoirAbonne.objects.filter(pk=abonne_id).first()

    def get_for_update(self, abonne_id: str) -> AvoirAbonne | None:
        """Avoir verrouillé (SELECT ... FOR UPDATE) — à appeler dans une
        transaction pour sérialiser l'imputation concurrente du crédit."""
        return AvoirAbonne.objects.select_for_update().filter(pk=abonne_id).first()

    def crediter(self, abonne_id: str, montant: Decimal) -> AvoirAbonne:
        """Ajoute `montant` au crédit de l'abonné (crée la ligne au besoin)."""
        from decimal import Decimal

        avoir, _ = AvoirAbonne.objects.get_or_create(abonne_id=abonne_id, defaults={"montant": Decimal("0")})
        avoir.montant = Decimal(str(avoir.montant)) + Decimal(str(montant))
        avoir.save(update_fields=["montant", "updated_at"])
        return avoir

    def consommer(self, avoir: AvoirAbonne, montant: Decimal) -> AvoirAbonne:
        """Décrémente le crédit de `montant` (borné à zéro)."""
        from decimal import Decimal

        restant = Decimal(str(avoir.montant)) - Decimal(str(montant))
        avoir.montant = restant if restant > 0 else Decimal("0")
        avoir.save(update_fields=["montant", "updated_at"])
        return avoir


class MouvementAvoirRepository:
    """Accès base de données pour le journal des mouvements d'avoir."""

    def create(
        self,
        abonne_id: str,
        montant: Decimal,
        type_mouvement: str,
        motif: str = "",
        facture_id: str = "",
        cree_par: str = "",
    ) -> MouvementAvoir:
        """Enregistre un mouvement d'avoir (crédit ou imputation)."""
        return MouvementAvoir.objects.create(
            abonne_id=abonne_id,
            montant=montant,
            type_mouvement=type_mouvement,
            motif=motif,
            facture_id=facture_id,
            cree_par=cree_par,
        )

    def list_by_abonne(self, abonne_id: str) -> list[MouvementAvoir]:
        """Journal des mouvements d'un abonné, du plus récent au plus ancien."""
        return list(MouvementAvoir.objects.filter(abonne_id=abonne_id))
