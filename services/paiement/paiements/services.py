"""Logique métier du Paiement Service."""

import logging
from datetime import date
from decimal import Decimal

from django.core.exceptions import ObjectDoesNotExist, ValidationError
from django.db import transaction

from .grpc_clients import (
    AbonneServiceClient,
    ConfigServiceClient,
    NotificationServiceClient,
)
from .models import (
    AvoirAbonne,
    ModePaiement,
    Paiement,
    SoldeFacture,
    StatutSolde,
    SuiviImpaye,
    TypeMouvementAvoir,
)
from .repositories import (
    AvoirAbonneRepository,
    MouvementAvoirRepository,
    PaiementRepository,
    SoldeFactureRepository,
    SuiviImpayeRepository,
)

logger = logging.getLogger(__name__)


class PaiementService:
    """Gestion des paiements et des soldes de factures."""

    def __init__(self) -> None:
        self._paiement_repo = PaiementRepository()
        self._solde_repo = SoldeFactureRepository()
        self._suivi_repo = SuiviImpayeRepository()
        self._avoir_repo = AvoirAbonneRepository()
        self._mouvement_repo = MouvementAvoirRepository()

    def initialiser_solde(
        self,
        facture_id: str,
        abonne_id: str,
        montant_total: float,
        date_limite_paiement: date,
        campagne_id: str = "",
    ) -> SoldeFacture:
        """
        Crée le SoldeFacture initial lors de la génération d'une facture.
        Appelé par Facturation Service via gRPC.
        """
        if not facture_id:
            raise ValidationError("L'identifiant de la facture est obligatoire.")
        if not abonne_id:
            raise ValidationError("L'identifiant de l'abonné est obligatoire.")
        montant_d = Decimal(str(montant_total))
        if montant_d <= 0:
            raise ValidationError("Le montant total doit être supérieur à zéro.")

        # Idempotent : si le solde existe déjà (ré-initialisation, réconciliation
        # d'une facture orpheline), on le renvoie tel quel — on n'écrase JAMAIS
        # les versements déjà enregistrés.
        existant = self._solde_repo.get_if_exists(facture_id)
        if existant is not None:
            return existant

        with transaction.atomic():
            solde = self._solde_repo.create(
                facture_id=facture_id,
                abonne_id=abonne_id,
                campagne_id=campagne_id,
                montant_total=montant_d,
                date_limite_paiement=date_limite_paiement,
            )
            # Report automatique de l'avoir disponible de l'abonné (trop-perçus
            # antérieurs) sur cette nouvelle facture.
            self._appliquer_avoir(solde, abonne_id)
        return solde

    def _appliquer_avoir(self, solde: SoldeFacture, abonne_id: str) -> None:
        """Impute l'avoir disponible de l'abonné sur le solde d'une facture
        nouvellement créée. L'imputation est enregistrée comme un versement de
        mode AVOIR (traçable dans l'historique des paiements) puis décrémente
        l'avoir. À appeler dans une transaction (verrou pris sur l'avoir)."""
        avoir = self._avoir_repo.get_for_update(abonne_id)
        if avoir is None:
            return
        a_imputer = min(Decimal(str(avoir.montant)), Decimal(str(solde.solde_restant)))
        if a_imputer <= 0:
            return

        self._paiement_repo.create(
            facture_id=solde.facture_id,
            abonne_id=abonne_id,
            montant=a_imputer,
            date_paiement=date.today(),
            mode_paiement=ModePaiement.AVOIR,
            reference_transaction="",
            enregistre_par="system",
        )
        self._solde_repo.update_after_paiement(solde, a_imputer)
        self._avoir_repo.consommer(avoir, a_imputer)
        self._mouvement_repo.create(abonne_id, a_imputer, TypeMouvementAvoir.IMPUTATION, facture_id=solde.facture_id)

    def crediter_avoir_manuel(self, abonne_id: str, montant: float, motif: str, cree_par: str) -> AvoirAbonne:
        """Émet un avoir manuel (note de rectification : facture corrigée à la
        baisse, erreur d'index, geste commercial). Le crédit alimente l'avoir de
        l'abonné — reporté automatiquement sur ses prochaines factures — et est
        tracé au journal (qui, combien, pourquoi)."""
        montant_d = Decimal(str(montant))
        if montant_d <= 0:
            raise ValidationError("Le montant de l'avoir doit être supérieur à zéro.")
        if not motif or not motif.strip():
            raise ValidationError("Le motif de la rectification est obligatoire.")

        with transaction.atomic():
            avoir = self._avoir_repo.crediter(abonne_id, montant_d)
            self._mouvement_repo.create(
                abonne_id, montant_d, TypeMouvementAvoir.RECTIFICATION, motif=motif.strip(), cree_par=cree_par
            )
        return avoir

    def get_avoir_abonne(self, abonne_id: str) -> tuple[Decimal, list]:
        """Retourne (solde d'avoir disponible, journal des mouvements) d'un abonné."""
        avoir = self._avoir_repo.get_if_exists(abonne_id)
        montant = Decimal(str(avoir.montant)) if avoir else Decimal("0")
        mouvements = self._mouvement_repo.list_by_abonne(abonne_id)
        return montant, mouvements

    def enregistrer_paiement(
        self,
        facture_id: str,
        abonne_id: str,
        montant: float,
        date_paiement: date,
        mode_paiement: str,
        reference_transaction: str,
        enregistre_par: str,
    ) -> tuple[Paiement, SoldeFacture]:
        """
        Enregistre un versement et met à jour le solde de la facture.

        Règles :
        - montant > 0
        - reference_transaction obligatoire pour MOBILE_MONEY et VIREMENT
        - un surpaiement (montant > solde restant) est accepté : la facture est
          soldée avec la part imputable et l'excédent est porté au crédit
          (avoir) de l'abonné, reporté sur ses prochaines factures.
        """
        # Validation du montant
        montant_d = Decimal(str(montant))
        if montant_d <= 0:
            raise ValidationError("Le montant du paiement doit être supérieur à zéro.")

        # Validation du mode et de la référence
        if mode_paiement in (ModePaiement.MOBILE_MONEY, ModePaiement.VIREMENT):
            if not reference_transaction or not reference_transaction.strip():
                raise ValidationError(f"La référence de transaction est obligatoire pour le mode {mode_paiement}.")

        # Récupération du solde, contrôle anti-surpaiement, création du versement
        # et recalcul du solde/statut dans une seule transaction : les deux
        # écritures (Paiement + SoldeFacture) commitent ensemble ou pas du tout.
        # Sans cela, un crash entre les deux laisse le SoldeFacture désynchronisé
        # du journal des versements (statut IMPAYEE alors que la facture est soldée).
        with transaction.atomic():
            # Récupération du solde — verrouillé (SELECT ... FOR UPDATE) pour
            # sérialiser les versements concurrents sur une même facture.
            solde = self._solde_repo.get_by_facture_id(facture_id, for_update=True)

            # Idempotence : si un paiement portant cette référence existe déjà
            # (rejeu réseau, double-clic), on renvoie l'existant SANS re-créditer.
            # La référence (MoMo/virement) est l'identifiant naturel de la
            # transaction ; la contrainte unique partielle en base est le filet ultime.
            reference = (reference_transaction or "").strip()
            if reference:
                existant = self._paiement_repo.get_by_reference(reference)
                if existant is not None:
                    return existant, solde

            # Trop-perçu : un versement supérieur au solde restant est accepté.
            # La facture est soldée avec la part imputable et l'excédent est
            # porté au crédit (avoir) de l'abonné — le solde d'une facture ne
            # devient jamais négatif.
            solde_restant = Decimal(str(solde.solde_restant))
            part_imputee = min(montant_d, solde_restant) if solde_restant > 0 else Decimal("0")
            excedent = montant_d - part_imputee

            # Enregistrement du paiement (montant réellement reçu)
            paiement = self._paiement_repo.create(
                facture_id=facture_id,
                abonne_id=abonne_id,
                montant=montant_d,
                date_paiement=date_paiement,
                mode_paiement=mode_paiement,
                reference_transaction=reference,
                enregistre_par=enregistre_par,
            )

            # Mise à jour du solde (part imputée) + report de l'excédent en avoir.
            solde = self._solde_repo.update_after_paiement(solde, part_imputee)
            if excedent > 0:
                self._avoir_repo.crediter(abonne_id, excedent)
                self._mouvement_repo.create(abonne_id, excedent, TypeMouvementAvoir.TROP_PERCU)

        return paiement, solde

    def enregistrer_paiement_abonne(
        self,
        abonne_id: str,
        montant: float,
        date_paiement: date,
        mode_paiement: str,
        reference_transaction: str,
        enregistre_par: str,
    ) -> tuple[list[Paiement], Decimal]:
        """Encaisse un versement au nom d'un abonné, imputé **du plus ancien au plus récent**.

        Jusqu'ici un versement visait une facture, choisie par le caissier. Dès
        qu'un abonné traîne plusieurs dettes — le cas dès qu'on saisit un
        arriéré antérieur à l'application — cela l'obligeait à ventiler à la
        main, et rien ne l'empêchait de solder la dette récente en laissant
        vieillir l'ancienne.

        L'imputation suit désormais la règle comptable usuelle : le solde le
        plus anciennement exigible s'éteint d'abord, le reliquat déborde sur le
        suivant. Un versement peut donc produire **plusieurs** écritures.

        Ce qui reste après extinction de toutes les dettes part en avoir, comme
        pour un trop-perçu sur facture unique.

        Retourne les versements créés et l'excédent porté au crédit.
        """
        montant_d = Decimal(str(montant))
        if montant_d <= 0:
            raise ValidationError("Le montant du paiement doit être supérieur à zéro.")
        if mode_paiement in (ModePaiement.MOBILE_MONEY, ModePaiement.VIREMENT):
            if not reference_transaction or not reference_transaction.strip():
                raise ValidationError(f"La référence de transaction est obligatoire pour le mode {mode_paiement}.")

        reference = (reference_transaction or "").strip()
        crees: list[Paiement] = []

        with transaction.atomic():
            # Idempotence : une référence déjà vue ne re-crédite rien. Le filet
            # vaut pour l'ensemble du versement, pas pour chaque imputation.
            if reference:
                existant = self._paiement_repo.get_by_reference(reference)
                if existant is not None:
                    return [existant], Decimal("0")

            soldes = self._solde_repo.list_non_soldes_par_abonne(abonne_id, for_update=True)
            restant = montant_d

            for solde in soldes:
                if restant <= 0:
                    break
                du = Decimal(str(solde.solde_restant))
                if du <= 0:
                    continue
                part = min(restant, du)
                # La référence ne se pose que sur la première écriture : la
                # contrainte d'unicité en base l'exige, et c'est bien un seul
                # versement même s'il se répartit sur plusieurs factures.
                crees.append(
                    self._paiement_repo.create(
                        facture_id=solde.facture_id,
                        abonne_id=abonne_id,
                        montant=part,
                        date_paiement=date_paiement,
                        mode_paiement=mode_paiement,
                        reference_transaction=reference if not crees else "",
                        enregistre_par=enregistre_par,
                    )
                )
                self._solde_repo.update_after_paiement(solde, part)
                restant -= part

            if restant > 0:
                self._avoir_repo.crediter(abonne_id, restant)
                self._mouvement_repo.create(abonne_id, restant, TypeMouvementAvoir.TROP_PERCU)

        return crees, restant

    def total_du_abonne(self, abonne_id: str, hors_facture_id: str = "") -> Decimal:
        """Ce qu'un abonné doit encore, toutes factures confondues.

        `hors_facture_id` sert à l'impression : sur une facture, le « solde
        antérieur » est ce qu'il doit **en plus** de celle qu'il tient en main.
        """
        return self._solde_repo.total_du_abonne(abonne_id, hors_facture_id)

    def annuler_paiement(self, paiement_id: str, motif: str, annule_par: str) -> tuple[Paiement, SoldeFacture]:
        """Annule un paiement (annulation douce) et rétablit le solde de la facture.

        Le paiement reste en base, marqué annulé (traçabilité qui/quand/pourquoi).
        Un paiement déjà annulé est refusé (pas de double rétablissement du solde).
        """
        with transaction.atomic():
            paiement = self._paiement_repo.get_by_id(paiement_id)
            if paiement.annule:
                raise ValidationError("Ce paiement est déjà annulé.")
            # Verrou du solde pour sérialiser avec les versements concurrents.
            solde = self._solde_repo.get_by_facture_id(paiement.facture_id, for_update=True)
            solde = self._solde_repo.update_after_annulation(solde, paiement.montant)
            paiement = self._paiement_repo.marquer_annule(paiement, motif=motif, annule_par=annule_par)
        return paiement, solde

    def get_solde(self, facture_id: str) -> SoldeFacture:
        """Retourne le solde courant d'une facture."""
        return self._solde_repo.get_by_facture_id(facture_id)

    def list_paiements(self, facture_id: str = "", abonne_id: str = "") -> list[Paiement]:
        """Liste les paiements, filtrés par facture et/ou abonné."""
        return self._paiement_repo.list_by_facture_and_abonne(facture_id, abonne_id)

    def list_paiements_par_campagne(self, campagne_id: str) -> list[Paiement]:
        """Liste tous les paiements des factures d'une campagne (export CSV)."""
        return self._paiement_repo.list_by_campagne(campagne_id)

    def list_impayes(self) -> list[SoldeFacture]:
        """
        Retourne les factures dont la date limite est dépassée et qui ne sont pas PAYEE.
        Utilisé par le cron et le grpc_server.
        """
        return self._solde_repo.list_impayes()

    def get_suivi_impaye(self, facture_id: str) -> SuiviImpaye:
        """Retourne le suivi d'impayé pour une facture."""
        return self._suivi_repo.get_by_facture_id(facture_id)

    def marquer_facture_payee_si_applicable(self, solde: SoldeFacture) -> None:
        """
        Après paiement total : résout le SuiviImpaye si existant,
        réactive l'abonné s'il était suspendu, envoie confirmation WhatsApp.
        """
        if solde.statut != StatutSolde.PAYEE:
            return

        # Résoudre le suivi impayé si existant
        try:
            suivi = self._suivi_repo.get_by_facture_id(solde.facture_id)
            suivi.resolu_le = date.today()
            self._suivi_repo.save_suivi(suivi)
            logger.info(
                "Suivi impayé résolu",
                extra={"facture_id": solde.facture_id},
            )
        except ObjectDoesNotExist:
            pass  # Pas de suivi impayé, c'est normal

        # EF-IMP-005 — Réactivation de l'abonné suspendu (dégradation gracieuse)
        AbonneServiceClient().reactiver_abonne(solde.abonne_id)

        notif_client = NotificationServiceClient()

        # Confirmation de paiement complet par WhatsApp (dégradation gracieuse)
        notif_client.envoyer_relance(
            facture_id=solde.facture_id,
            abonne_id=solde.abonne_id,
            etape=0,  # étape 0 = confirmation de paiement
        )

    def suspendre_relances_si_partiel(self, solde: SoldeFacture, jours_suspension: int | None = None) -> None:
        """
        Après paiement partiel : suspend les relances pendant N jours.

        `jours_suspension` est lu depuis Config Service (clé
        `impaye_suspension_relances`) lorsqu'il n'est pas fourni explicitement,
        avec repli sur la valeur par défaut si le service est indisponible.
        """
        from datetime import timedelta  # noqa: PLC0415 — timedelta déjà dans stdlib

        if solde.statut != StatutSolde.PARTIELLE:
            return

        if jours_suspension is None:
            jours_suspension = int(ConfigServiceClient().get_delais_impayes()["suspension_relances"])

        try:
            suivi = self._suivi_repo.get_by_facture_id(solde.facture_id)
            suivi.relances_suspendues_jusqu = date.today() + timedelta(days=jours_suspension)
            self._suivi_repo.save_suivi(suivi)
            logger.info(
                "Relances suspendues après paiement partiel",
                extra={
                    "facture_id": solde.facture_id,
                    "jours": jours_suspension,
                },
            )
        except ObjectDoesNotExist:
            pass  # Pas encore de suivi, rien à suspendre


class ImpayeService:
    """Gestion de l'escalade des impayés (cron 8h00)."""

    def __init__(self) -> None:
        self._solde_repo = SoldeFactureRepository()
        self._suivi_repo = SuiviImpayeRepository()

    def verifier_et_escalader(self) -> None:
        """
        Vérifie toutes les factures impayées et escalade les relances selon les délais configurés.

        Appelé par le cron APScheduler à 8h00 chaque matin.
        Délais récupérés depuis Config Service (avec valeurs par défaut).
        """
        # Récupération des délais depuis Config Service
        config_client = ConfigServiceClient()
        delais = config_client.get_delais_impayes()

        delai_rappel_1: int = delais.get("rappel_1", 0)
        delai_rappel_2: int = delais.get("rappel_2", 3)
        delai_avertissement: int = delais.get("avertissement", 7)
        delai_suspension: int = delais.get("suspension", 10)
        suspension_auto: bool = delais.get("suspension_auto", True)

        impayes = self._solde_repo.list_impayes()
        logger.info("ImpayeChecker : %d factures impayées à traiter", len(impayes))

        for solde in impayes:
            self._escalader_facture(
                solde=solde,
                delai_rappel_1=delai_rappel_1,
                delai_rappel_2=delai_rappel_2,
                delai_avertissement=delai_avertissement,
                delai_suspension=delai_suspension,
                suspension_auto=suspension_auto,
            )

    def _escalader_facture(
        self,
        solde: SoldeFacture,
        delai_rappel_1: int,
        delai_rappel_2: int,
        delai_avertissement: int,
        delai_suspension: int,
        suspension_auto: bool,
    ) -> None:
        """Escalade une facture impayée selon son étape actuelle."""
        from django.utils import timezone

        today = date.today()
        jours_depasses = (today - solde.date_limite_paiement).days

        suivi, _ = self._suivi_repo.get_or_create(
            facture_id=solde.facture_id,
            abonne_id=solde.abonne_id,
            date_depassement=solde.date_limite_paiement,
        )

        if suivi.relances_suspendues_jusqu and suivi.relances_suspendues_jusqu >= today:
            logger.debug(
                "Relances suspendues jusqu'au %s pour facture %s",
                suivi.relances_suspendues_jusqu,
                solde.facture_id,
            )
            return

        notif_client = NotificationServiceClient()
        modifie = False

        modifie |= self._tenter_rappel(notif_client, solde, suivi, timezone, jours_depasses, delai_rappel_1, 1)
        modifie |= self._tenter_rappel(notif_client, solde, suivi, timezone, jours_depasses, delai_rappel_2, 2)
        modifie |= self._tenter_rappel(notif_client, solde, suivi, timezone, jours_depasses, delai_avertissement, 3)

        if jours_depasses >= delai_suspension and not suivi.suspension_effectuee and suspension_auto:
            modifie |= self._effectuer_suspension(notif_client, solde, suivi, timezone)

        if modifie:
            self._suivi_repo.save_suivi(suivi)

    def _tenter_rappel(
        self,
        notif_client: NotificationServiceClient,
        solde: SoldeFacture,
        suivi: SuiviImpaye,
        timezone: object,
        jours_depasses: int,
        delai: int,
        etape: int,
    ) -> bool:
        """Envoie la relance de l'étape donnée si le délai est atteint et non encore envoyée."""
        _ETAPE_ATTRS: dict[int, tuple[str, str]] = {
            1: ("rappel_1_envoye", "date_rappel_1"),
            2: ("rappel_2_envoye", "date_rappel_2"),
            3: ("avertissement_envoye", "date_avertissement"),
        }
        sent_attr, date_attr = _ETAPE_ATTRS[etape]
        if jours_depasses < delai or getattr(suivi, sent_attr):
            return False
        notif_client.envoyer_relance(
            facture_id=solde.facture_id,
            abonne_id=solde.abonne_id,
            etape=etape,
        )
        setattr(suivi, sent_attr, True)
        setattr(suivi, date_attr, timezone.now())
        suivi.etape_actuelle = max(suivi.etape_actuelle, etape)
        logger.info("Relance étape %d envoyée — facture %s", etape, solde.facture_id)
        return True

    def _effectuer_suspension(
        self,
        notif_client: NotificationServiceClient,
        solde: SoldeFacture,
        suivi: SuiviImpaye,
        timezone: object,
    ) -> bool:
        """Suspend l'abonné, envoie la relance étape 4 et notifie les admins."""
        AbonneServiceClient().suspendre_abonne(solde.abonne_id)
        notif_client.envoyer_relance(
            facture_id=solde.facture_id,
            abonne_id=solde.abonne_id,
            etape=4,
        )
        # EF-NOTIF-005 — Notifier les admins de chaque suspension
        notif_client.notifier_admins(
            evenement="SUSPENSION",
            detail=f"Abonné {solde.abonne_id} suspendu pour impayé (facture {solde.facture_id})",
            entite_id=solde.abonne_id,
        )
        suivi.suspension_effectuee = True
        suivi.date_suspension = timezone.now()
        suivi.etape_actuelle = max(suivi.etape_actuelle, 4)
        logger.info("Suspension effectuée — facture %s, abonné %s", solde.facture_id, solde.abonne_id)
        return True
