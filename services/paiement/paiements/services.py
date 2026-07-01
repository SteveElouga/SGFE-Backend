"""Logique métier du Paiement Service."""

import logging
from datetime import date
from decimal import Decimal

from django.core.exceptions import ObjectDoesNotExist, ValidationError

from .grpc_clients import (
    AbonneServiceClient,
    ConfigServiceClient,
    NotificationServiceClient,
)
from .models import ModePaiement, Paiement, SoldeFacture, StatutSolde, SuiviImpaye
from .repositories import (
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

    def initialiser_solde(
        self,
        facture_id: str,
        abonne_id: str,
        montant_total: float,
        date_limite_paiement: date,
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

        return self._solde_repo.create(
            facture_id=facture_id,
            abonne_id=abonne_id,
            montant_total=montant_d,
            date_limite_paiement=date_limite_paiement,
        )

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
        - montant <= solde_restant (pas de surpaiement)
        - reference_transaction obligatoire pour MOBILE_MONEY et VIREMENT
        """
        # Validation du montant
        montant_d = Decimal(str(montant))
        if montant_d <= 0:
            raise ValidationError("Le montant du paiement doit être supérieur à zéro.")

        # Validation du mode et de la référence
        if mode_paiement in (ModePaiement.MOBILE_MONEY, ModePaiement.VIREMENT):
            if not reference_transaction or not reference_transaction.strip():
                raise ValidationError(
                    f"La référence de transaction est obligatoire pour le mode {mode_paiement}."
                )

        # Récupération du solde
        solde = self._solde_repo.get_by_facture_id(facture_id)

        # Vérification du surpaiement
        solde_restant = Decimal(str(solde.solde_restant))
        if montant_d > solde_restant:
            raise ValidationError(
                f"Le montant versé ({montant_d}) dépasse le solde restant ({solde_restant})."
            )

        # Enregistrement du paiement
        paiement = self._paiement_repo.create(
            facture_id=facture_id,
            abonne_id=abonne_id,
            montant=montant_d,
            date_paiement=date_paiement,
            mode_paiement=mode_paiement,
            reference_transaction=reference_transaction or "",
            enregistre_par=enregistre_par,
        )

        # Mise à jour du solde
        solde = self._solde_repo.update_after_paiement(solde, montant_d)

        return paiement, solde

    def get_solde(self, facture_id: str) -> SoldeFacture:
        """Retourne le solde courant d'une facture."""
        return self._solde_repo.get_by_facture_id(facture_id)

    def list_paiements(
        self, facture_id: str = "", abonne_id: str = ""
    ) -> list[Paiement]:
        """Liste les paiements, filtrés par facture et/ou abonné."""
        return self._paiement_repo.list_by_facture_and_abonne(facture_id, abonne_id)

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
        Après paiement total : résout le SuiviImpaye si existant.
        Appelle Notification Service (dégradation gracieuse).
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

        # Notification de paiement complet (dégradation gracieuse)
        try:
            notif_client = NotificationServiceClient()
            notif_client.envoyer_relance(
                facture_id=solde.facture_id,
                abonne_id=solde.abonne_id,
                etape=0,  # étape 0 = confirmation de paiement
            )
        except Exception as exc:
            logger.warning(
                "Notification paiement complet échouée — dégradation gracieuse",
                extra={"facture_id": solde.facture_id, "error": str(exc)},
            )

    def suspendre_relances_si_partiel(
        self, solde: SoldeFacture, jours_suspension: int = 5
    ) -> None:
        """
        Après paiement partiel : suspend les relances pendant N jours.
        """
        from datetime import timedelta  # noqa: PLC0415 — timedelta déjà dans stdlib

        if solde.statut != StatutSolde.PARTIELLE:
            return

        try:
            suivi = self._suivi_repo.get_by_facture_id(solde.facture_id)
            suivi.relances_suspendues_jusqu = date.today() + timedelta(
                days=jours_suspension
            )
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
        suspension_relances: int = delais.get("suspension_relances", 5)

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
                suspension_relances=suspension_relances,
            )

    def _escalader_facture(
        self,
        solde: SoldeFacture,
        delai_rappel_1: int,
        delai_rappel_2: int,
        delai_avertissement: int,
        delai_suspension: int,
        suspension_auto: bool,
        suspension_relances: int,
    ) -> None:
        """Escalade une facture impayée selon son étape actuelle."""

        from django.utils import timezone

        today = date.today()
        jours_depasses = (today - solde.date_limite_paiement).days

        # Récupération ou création du suivi
        suivi, _ = self._suivi_repo.get_or_create(
            facture_id=solde.facture_id,
            abonne_id=solde.abonne_id,
            date_depassement=solde.date_limite_paiement,
        )

        # Vérification de la suspension des relances
        if suivi.relances_suspendues_jusqu and suivi.relances_suspendues_jusqu >= today:
            logger.debug(
                "Relances suspendues jusqu'au %s pour facture %s",
                suivi.relances_suspendues_jusqu,
                solde.facture_id,
            )
            return

        notif_client = NotificationServiceClient()
        modifie = False

        # Étape 1 — 1er rappel (J+delai_rappel_1)
        if jours_depasses >= delai_rappel_1 and not suivi.rappel_1_envoye:
            try:
                notif_client.envoyer_relance(
                    facture_id=solde.facture_id,
                    abonne_id=solde.abonne_id,
                    etape=1,
                )
                suivi.rappel_1_envoye = True
                suivi.date_rappel_1 = timezone.now()
                suivi.etape_actuelle = max(suivi.etape_actuelle, 1)
                modifie = True
                logger.info("Rappel 1 envoyé — facture %s", solde.facture_id)
            except Exception as exc:
                logger.warning(
                    "Rappel 1 échoué — dégradation gracieuse",
                    extra={"facture_id": solde.facture_id, "error": str(exc)},
                )

        # Étape 2 — 2ème rappel (J+delai_rappel_2)
        if jours_depasses >= delai_rappel_2 and not suivi.rappel_2_envoye:
            try:
                notif_client.envoyer_relance(
                    facture_id=solde.facture_id,
                    abonne_id=solde.abonne_id,
                    etape=2,
                )
                suivi.rappel_2_envoye = True
                suivi.date_rappel_2 = timezone.now()
                suivi.etape_actuelle = max(suivi.etape_actuelle, 2)
                modifie = True
                logger.info("Rappel 2 envoyé — facture %s", solde.facture_id)
            except Exception as exc:
                logger.warning(
                    "Rappel 2 échoué — dégradation gracieuse",
                    extra={"facture_id": solde.facture_id, "error": str(exc)},
                )

        # Étape 3 — Avertissement (J+delai_avertissement)
        if jours_depasses >= delai_avertissement and not suivi.avertissement_envoye:
            try:
                notif_client.envoyer_relance(
                    facture_id=solde.facture_id,
                    abonne_id=solde.abonne_id,
                    etape=3,
                )
                suivi.avertissement_envoye = True
                suivi.date_avertissement = timezone.now()
                suivi.etape_actuelle = max(suivi.etape_actuelle, 3)
                modifie = True
                logger.info("Avertissement envoyé — facture %s", solde.facture_id)
            except Exception as exc:
                logger.warning(
                    "Avertissement échoué — dégradation gracieuse",
                    extra={"facture_id": solde.facture_id, "error": str(exc)},
                )

        # Étape 4 — Suspension (J+delai_suspension)
        if (
            jours_depasses >= delai_suspension
            and not suivi.suspension_effectuee
            and suspension_auto
        ):
            abonne_client = AbonneServiceClient()
            try:
                abonne_client.suspendre_abonne(solde.abonne_id)
            except Exception as exc:
                logger.warning(
                    "Suspension abonné échouée — dégradation gracieuse",
                    extra={"abonne_id": solde.abonne_id, "error": str(exc)},
                )
            try:
                notif_client.envoyer_relance(
                    facture_id=solde.facture_id,
                    abonne_id=solde.abonne_id,
                    etape=4,
                )
            except Exception as exc:
                logger.warning(
                    "Notification suspension échouée — dégradation gracieuse",
                    extra={"facture_id": solde.facture_id, "error": str(exc)},
                )
            suivi.suspension_effectuee = True
            suivi.date_suspension = timezone.now()
            suivi.etape_actuelle = max(suivi.etape_actuelle, 4)
            modifie = True
            logger.info(
                "Suspension effectuée — facture %s, abonné %s",
                solde.facture_id,
                solde.abonne_id,
            )

        if modifie:
            self._suivi_repo.save_suivi(suivi)
