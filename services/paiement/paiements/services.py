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

            # Vérification du surpaiement
            solde_restant = Decimal(str(solde.solde_restant))
            if montant_d > solde_restant:
                raise ValidationError(f"Le montant versé ({montant_d}) dépasse le solde restant ({solde_restant}).")

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

    def list_paiements(self, facture_id: str = "", abonne_id: str = "") -> list[Paiement]:
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
