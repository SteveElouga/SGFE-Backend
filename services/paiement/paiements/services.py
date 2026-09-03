"""Logique métier du Paiement Service."""

import logging
import uuid
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


def _borne_ou_none(valeur: str, nom: str) -> date | None:
    """Parse une borne de période ISO, ou `None` si elle est vide.

    Une date illisible lève plutôt que d'être ignorée : un journal
    silencieusement non borné rendrait tout l'historique là où le comptable a
    demandé un mois, et rien ne le lui dirait avant qu'il somme la colonne.
    """
    if not valeur:
        return None
    try:
        return date.fromisoformat(valeur)
    except ValueError as exc:
        raise ValidationError(f"{nom} doit être une date ISO AAAA-MM-JJ (reçu : {valeur!r}).") from exc


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

    def annuler_solde(self, facture_id: str, motif: str) -> tuple[SoldeFacture, Decimal]:
        """Éteint le solde d'une facture annulée et rend à l'abonné ce qu'il a versé.

        Une facture peut être annulée après qu'un abonné a commencé à la payer —
        c'est même le cas le plus fréquent, puisqu'une erreur d'index se découvre
        souvent quand quelqu'un vient régler. Ce qu'il a versé ne lui appartient
        pas moins pour autant : le montant bascule à son avoir, d'où il
        s'imputera de lui-même sur la facture suivante.

        Le solde passe en ANNULEE et non en PAYEE. La distinction n'est pas
        décorative : « payée » et « annulée » racontent deux histoires opposées,
        et les confondre ferait apparaître dans les recettes une somme que
        personne n'a versée.

        Returns:
            Le solde éteint, et le montant porté à l'avoir (zéro si rien n'avait
            été versé).
        """
        with transaction.atomic():
            solde = self._solde_repo.get_by_facture_id(facture_id, for_update=True)
            if solde.statut == StatutSolde.ANNULEE:
                # Idempotent : réannuler ne doit pas re-créditer l'abonné.
                return solde, Decimal("0")

            deja_verse = Decimal(str(solde.montant_paye))
            self._solde_repo.annuler(solde)

            if deja_verse > 0:
                self._avoir_repo.crediter(solde.abonne_id, deja_verse)
                self._mouvement_repo.create(
                    solde.abonne_id,
                    deja_verse,
                    TypeMouvementAvoir.ANNULATION,
                    facture_id=facture_id,
                )
                logger.info(
                    "Versements d'une facture annulée portés à l'avoir",
                    extra={"facture_id": facture_id, "montant": str(deja_verse), "motif": motif},
                )
        return solde, deja_verse

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

        Ordre d'imputation — **la facture visée, puis les impayés, puis l'avoir.**

        Un abonné paie ce qu'on lui a demandé : la facture du mois dont il a reçu
        le message. Le versement s'impute donc d'abord sur celle-là. Ce qui
        dépasse ne devient pas un crédit tant qu'il reste une dette : l'excédent
        éteint les impayés, du plus anciennement exigible au plus récent.

        **Il ne peut donc y avoir d'avoir que si tous les impayés sont soldés.**
        Avant cette règle, un versement de 10 000 sur une facture de 5 000
        créditait 5 000 à un abonné qui devait encore 3 000 par ailleurs — il
        restait relancé pour une dette que son propre argent couvrait déjà.

        Un versement produit donc potentiellement **plusieurs écritures**, une par
        facture touchée. Elles partagent un `versement_id` : c'est ce qui permet
        de les annuler d'un bloc, puisqu'elles ne forment qu'un seul versement.

        Règles :
        - montant > 0
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

            # Idempotence : si un paiement portant cette référence existe déjà
            # (rejeu réseau, double-clic), on renvoie l'existant SANS re-créditer.
            # La référence (MoMo/virement) est l'identifiant naturel de la
            # transaction ; la contrainte unique partielle en base est le filet ultime.
            reference = (reference_transaction or "").strip()
            if reference:
                existant = self._paiement_repo.get_by_reference(reference)
                if existant is not None:
                    return existant, solde

            versement_id = uuid.uuid4()

            # 1) La facture visée d'abord — c'est celle que l'abonné règle.
            solde_restant = Decimal(str(solde.solde_restant))
            part_imputee = min(montant_d, solde_restant) if solde_restant > 0 else Decimal("0")

            paiement = self._paiement_repo.create(
                facture_id=facture_id,
                abonne_id=abonne_id,
                montant=part_imputee,
                date_paiement=date_paiement,
                mode_paiement=mode_paiement,
                reference_transaction=reference,
                enregistre_par=enregistre_par,
                versement_id=versement_id,
            )
            solde = self._solde_repo.update_after_paiement(solde, part_imputee)

            # 2) Puis les impayés, du plus anciennement exigible au plus récent.
            restant = montant_d - part_imputee
            if restant > 0:
                restant = self._cascader_sur_impayes(
                    abonne_id=abonne_id,
                    montant=restant,
                    hors_facture_id=facture_id,
                    date_paiement=date_paiement,
                    mode_paiement=mode_paiement,
                    enregistre_par=enregistre_par,
                    versement_id=versement_id,
                )

            # 3) Ce qui reste, et seulement lui, devient un avoir.
            if restant > 0:
                self._porter_en_avoir(abonne_id, restant, versement_id)
                # L'excédent est posé sur la dernière écriture du versement, qui
                # peut être celle-ci. Sans ce rafraîchissement, l'objet rendu à
                # l'appelant — et sérialisé par le serveur gRPC — annoncerait un
                # excédent nul alors que la base en porte un.
                paiement.refresh_from_db()

        return paiement, solde

    def _cascader_sur_impayes(
        self,
        abonne_id: str,
        montant: Decimal,
        hors_facture_id: str,
        date_paiement: date,
        mode_paiement: str,
        enregistre_par: str,
        versement_id: object,
    ) -> Decimal:
        """Impute `montant` sur les impayés de l'abonné et rend ce qui reste.

        Du plus anciennement **exigible** au plus récent : c'est l'ancienneté qui
        déclenche relances et suspension, donc éteindre la dette récente en
        laissant vieillir l'ancienne serait exactement le mauvais ordre.

        `hors_facture_id` exclut la facture déjà servie à l'étape 1 — son solde
        vient d'être mis à jour, la relire ici la ferait imputer deux fois.

        Chaque imputation crée sa propre écriture, sans référence de transaction :
        la contrainte d'unicité l'exige, et c'est bien un seul versement même
        réparti sur plusieurs factures.
        """
        restant = montant
        for solde in self._solde_repo.list_non_soldes_par_abonne(abonne_id, for_update=True):
            if restant <= 0:
                break
            if solde.facture_id == hors_facture_id:
                continue
            du = Decimal(str(solde.solde_restant))
            if du <= 0:
                continue
            part = min(restant, du)
            self._paiement_repo.create(
                facture_id=solde.facture_id,
                abonne_id=abonne_id,
                montant=part,
                date_paiement=date_paiement,
                mode_paiement=mode_paiement,
                reference_transaction="",
                enregistre_par=enregistre_par,
                versement_id=versement_id,
            )
            self._solde_repo.update_after_paiement(solde, part)
            restant -= part
        return restant

    def _porter_en_avoir(self, abonne_id: str, montant: Decimal, versement_id: object) -> None:
        """Crédite l'avoir du reliquat d'un versement, une fois toutes les dettes éteintes.

        L'excédent se rattache à la **dernière** écriture du versement : c'est
        elle qu'on annulera pour reprendre le crédit, et chaque autre écriture ne
        rend ainsi que ce qu'elle a réellement imputé.
        """
        self._avoir_repo.crediter(abonne_id, montant)
        self._mouvement_repo.create(abonne_id, montant, TypeMouvementAvoir.TROP_PERCU)
        dernier = self._paiement_repo.dernier_du_versement(versement_id)
        if dernier is not None:
            dernier.montant_excedent = montant
            dernier.save(update_fields=["montant_excedent"])

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

            versement_id = uuid.uuid4()
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
                        versement_id=versement_id,
                    )
                )
                self._solde_repo.update_after_paiement(solde, part)
                restant -= part

            if restant > 0:
                self._porter_en_avoir(abonne_id, restant, versement_id)

        return crees, restant

    def imputations_du_versement(self, versement_id: object) -> list[tuple[Paiement, SoldeFacture]]:
        """Les écritures d'un versement, chacune avec le solde de la facture qu'elle a touchée.

        Un versement produit une écriture par facture touchée. Les conséquences
        d'un encaissement — statut de la facture, résolution du suivi d'impayé,
        pause des relances, statistiques — sont **par facture**, pas par
        versement : elles doivent donc être appliquées sur chacune.

        C'est ce que cette méthode rend possible, et c'est ce qui manquait. Avant,
        l'appelant ne connaissait que la facture visée : une vieille facture
        éteinte par la cascade restait affichée IMPAYÉE dans le back-office, son
        `SuiviImpaye` n'était jamais résolu, et aucun reçu ne la couvrait.

        Les soldes sont relus après imputation : ils portent donc le statut
        définitif du versement, cascade comprise.
        """
        imputations: list[tuple[Paiement, SoldeFacture]] = []
        for paiement in self._paiement_repo.list_du_versement(versement_id):
            solde = self._solde_repo.get_if_exists(paiement.facture_id)
            if solde is not None:
                imputations.append((paiement, solde))
        return imputations

    def list_non_soldes_par_abonne(self, abonne_id: str) -> list[SoldeFacture]:
        """Soldes non éteints d'un abonné, du plus ancien exigible au plus récent."""
        return self._solde_repo.list_non_soldes_par_abonne(abonne_id)

    def total_du_abonne(self, abonne_id: str, hors_facture_id: str = "") -> Decimal:
        """Ce qu'un abonné doit encore, toutes factures confondues.

        `hors_facture_id` sert à l'impression : sur une facture, le « solde
        antérieur » est ce qu'il doit **en plus** de celle qu'il tient en main.
        """
        return self._solde_repo.total_du_abonne(abonne_id, hors_facture_id)

    def annuler_paiement(self, paiement_id: str, motif: str, annule_par: str) -> tuple[Paiement, SoldeFacture]:
        """Annule un paiement (annulation douce) et défait ce qu'il avait produit.

        Le paiement reste en base, marqué annulé (traçabilité qui/quand/pourquoi).
        Un paiement déjà annulé est refusé (pas de double rétablissement).

        Un versement a jusqu'à trois conséquences, et les trois se défont ici :

        1. **le solde de la facture**, rétabli de la seule part imputée — pas du
           montant reçu. Un versement de 10 000 sur une facture de 5 000 n'a
           imputé que 5 000 ; rétablir 10 000 s'appuyait sur un garde-fou
           anti-négatif au lieu d'être juste.

        2. **l'avoir de l'abonné**, débité de l'excédent qui y était parti.
           Sans cela, on rendait 10 000 à l'abonné en lui laissant 5 000 de
           crédit — mesuré avant correction.

        3. **le suivi d'impayé**, rouvert quand la facture n'est plus soldée.
           C'était le manque le plus coûteux : `resolu_le` restait daté, donc le
           cron de 8 h sautait la facture. La dette était rétablie et plus
           jamais relancée — silencieusement, en vieillissant.

        Raises:
            ValidationError: si l'excédent a déjà été consommé par une facture
                suivante. Voir `_reprendre_excedent`.
        """
        with transaction.atomic():
            demande = self._paiement_repo.get_by_id(paiement_id)
            if demande.annule:
                raise ValidationError("Ce paiement est déjà annulé.")

            # On annule le VERSEMENT, pas l'écriture.
            #
            # Un versement s'impute sur la facture visée, puis sur les impayés :
            # il produit donc plusieurs écritures. N'annuler que celle qu'on a
            # cliquée laisserait les autres imputations debout — un solde faux,
            # exactement le défaut qu'on venait de corriger sur le trop-perçu.
            ecritures = self._paiement_repo.list_du_versement(demande.versement_id)
            actives = [e for e in ecritures if not e.annule]

            excedent_total = sum((Decimal(str(e.montant_excedent or 0)) for e in actives), Decimal("0"))

            # L'avoir se reprend AVANT toute écriture : si l'excédent a déjà été
            # dépensé, l'annulation est refusée en bloc plutôt que faite à moitié.
            if excedent_total > 0:
                self._reprendre_excedent(demande.abonne_id, excedent_total)

            solde_demande = None
            for ecriture in actives:
                # Verrou du solde pour sérialiser avec les versements concurrents.
                solde = self._solde_repo.get_by_facture_id(ecriture.facture_id, for_update=True)
                solde = self._solde_repo.update_after_annulation(solde, Decimal(str(ecriture.montant)))
                self._paiement_repo.marquer_annule(ecriture, motif=motif, annule_par=annule_par)
                self._rouvrir_suivi_impaye(solde)
                if ecriture.id == demande.id:
                    solde_demande = solde

            paiement = self._paiement_repo.get_by_id(paiement_id)

        return paiement, solde_demande

    def _reprendre_excedent(self, abonne_id: str, excedent: Decimal) -> None:
        """Débite l'avoir de l'excédent d'un versement qu'on annule.

        Refuse si l'avoir ne le porte plus. Ce cas arrive dès qu'une facture
        suivante a été créée entre-temps : son solde a consommé l'avoir à sa
        naissance, et l'excédent n'existe plus sous forme de crédit.

        Le rendre malgré tout demanderait de remonter la chaîne d'imputation —
        rétablir le solde de la facture suivante, qui a peut-être déjà été
        relancée, voire soldée par d'autres versements. Ce n'est pas une
        correction qu'on improvise dans une transaction : **un refus explicite
        vaut mieux qu'un solde faux.**
        """
        avoir = self._avoir_repo.get_for_update(abonne_id)
        disponible = Decimal(str(avoir.montant)) if avoir is not None else Decimal("0")
        if disponible < excedent:
            raise ValidationError(
                f"Annulation impossible : le trop-perçu de {excedent} a déjà été imputé sur une "
                f"facture suivante (avoir disponible : {disponible}). Annulez d'abord cette "
                "imputation, ou émettez un avoir de rectification."
            )
        self._avoir_repo.consommer(avoir, excedent)
        self._mouvement_repo.create(abonne_id, excedent, TypeMouvementAvoir.REPRISE_TROP_PERCU)

    def _rouvrir_suivi_impaye(self, solde: SoldeFacture) -> None:
        """Rouvre le suivi d'impayé d'une facture qui n'est plus soldée.

        Le cron de relance ignore un suivi dont `resolu_le` est daté. Une facture
        rétablie en IMPAYEE ou PARTIELLE doit donc redevenir relançable — sinon
        la dette existe et rien ne s'en occupe.

        La resuspension de l'abonné n'est pas faite ici : c'est le cron qui la
        décide, à l'étape 4, en fonction de l'ancienneté. Le rouvrir suffit à le
        remettre sur ce chemin, et évite de resuspendre quelqu'un dont la dette
        n'a qu'un jour.
        """
        if solde.statut == StatutSolde.PAYEE:
            return
        try:
            suivi = self._suivi_repo.get_by_facture_id(solde.facture_id)
        except ObjectDoesNotExist:
            return  # jamais tombée en impayé, rien à rouvrir
        if suivi.resolu_le is None:
            return
        suivi.resolu_le = None
        self._suivi_repo.save_suivi(suivi)
        logger.info(
            "Suivi impayé rouvert après annulation d'un paiement",
            extra={"facture_id": solde.facture_id, "statut": solde.statut},
        )

    def get_solde(self, facture_id: str) -> SoldeFacture:
        """Retourne le solde courant d'une facture."""
        return self._solde_repo.get_by_facture_id(facture_id)

    def list_paiements(
        self,
        facture_id: str = "",
        abonne_id: str = "",
        date_debut: str = "",
        date_fin: str = "",
        limit: int | None = None,
        offset: int | None = None,
    ) -> list[Paiement]:
        """Liste les paiements, filtrés par facture, abonné et/ou période.

        `date_debut` / `date_fin` : bornes ISO `AAAA-MM-JJ` incluses, sur la date
        de paiement. C'est ce qu'un journal de caisse demande — et le seul chemin
        par lequel un paiement de régularisation devient exportable, son
        `SoldeFacture` portant un `campagne_id` vide.

        `limit`/`offset` optionnels : omis, la liste complète filtrée est
        retournée — comportement historique préservé à l'identique.
        """
        return self._paiement_repo.list_by_facture_and_abonne(
            facture_id,
            abonne_id,
            date_debut=_borne_ou_none(date_debut, "date_debut"),
            date_fin=_borne_ou_none(date_fin, "date_fin"),
            limit=limit,
            offset=offset,
        )

    def count_paiements(
        self,
        facture_id: str = "",
        abonne_id: str = "",
        date_debut: str = "",
        date_fin: str = "",
    ) -> int:
        """Nombre total de paiements correspondant au filtre, indépendamment de
        toute pagination."""
        return self._paiement_repo.count_by_facture_and_abonne(
            facture_id,
            abonne_id,
            date_debut=_borne_ou_none(date_debut, "date_debut"),
            date_fin=_borne_ou_none(date_fin, "date_fin"),
        )

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
        """Après paiement total d'UNE facture : résout son suivi d'impayé.

        Ne touche plus à l'abonné ni à ses notifications — c'est le rôle de
        `retablir_si_dette_eteinte`, appelée une fois par versement.

        Pourquoi ce partage. Cette méthode s'applique désormais à **chaque**
        facture éteinte par un versement, cascade comprise. Y laisser la
        réactivation et le message WhatsApp aurait produit, pour un abonné qui
        solde trois arriérés au comptoir, trois réactivations et trois messages
        « votre facture est réglée » — pour un seul geste, et alors que ce qui
        l'intéresse est de savoir s'il est à jour, une fois.
        """
        if solde.statut != StatutSolde.PAYEE:
            return

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

    def retablir_si_dette_eteinte(self, abonne_id: str, facture_id_reference: str) -> bool:
        """Réactive l'abonné et le prévient — mais seulement s'il ne doit plus RIEN.

        RS-005 (`docs/SRS.md`) dit « **paiement intégral** après suspension →
        ACTIF ». Le code lisait cette règle au niveau d'une facture : dès qu'UNE
        facture passait PAYEE, l'abonné était réactivé.

        Un abonné suspendu qui devait trois factures et n'en réglait qu'une
        retrouvait donc l'eau en devant encore deux mois. Perte de recette
        directe, et lecture fausse de la règle : « intégral » qualifie la dette,
        pas une ligne de la dette.

        La condition est maintenant `total_du_abonne(...) <= 0`. La méthode
        existait déjà (`total_du_abonne`, plus bas) et n'était appelée que pour
        l'impression des factures.

        Appelée une fois par versement, après que toutes les imputations ont été
        écrites : le total est donc définitif.

        Rend `True` si la dette est éteinte — ce que l'appelant utilise pour
        décider du contenu du reçu.
        """
        reste = self.total_du_abonne(abonne_id)
        if reste > 0:
            logger.info(
                "Pas de rétablissement — l'abonné doit encore",
                extra={"abonne_id": abonne_id, "reste_du": str(reste)},
            )
            return False

        # EF-IMP-005 — Réactivation de l'abonné suspendu (dégradation gracieuse).
        # Rend False s'il n'était pas suspendu — le cas le plus fréquent.
        retabli = AbonneServiceClient().reactiver_abonne(abonne_id)

        # EF-NOTIF-004 — le message de RÉTABLISSEMENT, et seulement sur un
        # rétablissement réel.
        #
        # Il partait à chaque facture soldée, en affirmant « votre ligne d'eau est
        # maintenant rétablie » à des abonnés qui n'avaient jamais été coupés. Et
        # il doublait le reçu, qui part de toute façon : deux messages pour un
        # geste, annonçant deux montants différents du même versement.
        #
        # Le reçu confirme l'argent ; celui-ci ne confirme que l'eau qui revient.
        if retabli:
            NotificationServiceClient().envoyer_relance(
                facture_id=facture_id_reference,
                abonne_id=abonne_id,
                etape=0,
            )
        return True

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

        # ── UN seul message, celui qui correspond au retard RÉEL ─────────────
        #
        # Les trois rappels étaient tentés dans le même passage, puis la
        # suspension. Pour une facture déjà très en retard — le cas dès qu'on
        # saisit un arriéré avec sa vraie échéance — le premier passage envoyait
        # quatre messages en quelques secondes, qui se contredisaient :
        #
        #   « arrivée à échéance aujourd'hui »
        #   « impayée depuis 3 jours »
        #   « impayé depuis 7 jours … suspendue dans 3 jours »
        #   « votre ligne d'eau a été suspendue »
        #
        # On ne rejoue donc plus les étapes manquées : on envoie **la plus
        # avancée que le retard justifie**, et elle seule. Un abonné à 31 jours
        # de retard reçoit l'avis de suspension, pas quatre messages dont trois
        # sont faux.
        #
        # Sur une facture qui vieillit normalement, le comportement est
        # inchangé : étape 1 au jour 0, étape 2 au jour 3, etc. — à chaque
        # passage, l'étape la plus avancée justifiée est aussi la suivante.
        #
        # Les drapeaux des étapes sautées restent à False, ce qui est la vérité :
        # ces messages n'ont jamais été envoyés. `etape_actuelle` porte, lui, le
        # niveau réellement atteint.
        modifie = False

        if jours_depasses >= delai_suspension and not suivi.suspension_effectuee and suspension_auto:
            modifie = self._effectuer_suspension(notif_client, solde, suivi, timezone)
        elif jours_depasses >= delai_avertissement:
            modifie = self._tenter_rappel(
                notif_client,
                solde,
                suivi,
                timezone,
                jours_depasses,
                delai_avertissement,
                3,
                # Le vrai nombre de jours restants, lu dans Config par ce cron.
                # Le gabarit écrivait « dans 3 jours » en dur.
                jours_avant_suspension=max(0, delai_suspension - jours_depasses),
            )
        elif jours_depasses >= delai_rappel_2:
            modifie = self._tenter_rappel(notif_client, solde, suivi, timezone, jours_depasses, delai_rappel_2, 2)
        elif jours_depasses >= delai_rappel_1:
            modifie = self._tenter_rappel(notif_client, solde, suivi, timezone, jours_depasses, delai_rappel_1, 1)

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
        jours_avant_suspension: int = 0,
    ) -> bool:
        """Envoie la relance de l'étape donnée si le délai est atteint et non encore envoyée.

        **L'étape n'est marquée envoyée que si le message est réellement parti.**

        Elle l'était inconditionnellement, alors que `envoyer_relance` n'échoue
        jamais : les erreurs gRPC comme les échecs WhatsApp sont avalés. Un abonné
        pouvait donc être relancé quatre fois sans rien recevoir, puis coupé —
        et l'escalade continuait comme si tout lui avait été dit.

        Le drapeau n'étant plus posé, le passage du lendemain retentera la même
        étape. Une relance qui n'est pas partie n'est pas une relance.
        """
        _ETAPE_ATTRS: dict[int, tuple[str, str]] = {
            1: ("rappel_1_envoye", "date_rappel_1"),
            2: ("rappel_2_envoye", "date_rappel_2"),
            3: ("avertissement_envoye", "date_avertissement"),
        }
        sent_attr, date_attr = _ETAPE_ATTRS[etape]
        if jours_depasses < delai or getattr(suivi, sent_attr):
            return False

        parti = notif_client.envoyer_relance(
            facture_id=solde.facture_id,
            abonne_id=solde.abonne_id,
            etape=etape,
            jours_avant_suspension=jours_avant_suspension,
        )
        if not parti:
            logger.warning(
                "Relance étape %d NON partie — l'étape reste à retenter",
                etape,
                extra={"facture_id": solde.facture_id, "abonne_id": solde.abonne_id},
            )
            return False

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
        """Suspend l'abonné, envoie la relance étape 4 et notifie les admins.

        La suspension est effectuée même si le message ne part pas : la coupure
        est la décision, le message n'en est que l'annonce, et différer la
        coupure parce que WhatsApp est en panne serait perdre de la recette.

        Mais l'échec est journalisé en ERREUR, et les admins sont prévenus dans
        tous les cas : quelqu'un a été coupé sans l'avoir appris, il faut que
        ça se sache avant qu'il appelle.
        """
        AbonneServiceClient().suspendre_abonne(solde.abonne_id)
        prevenu = notif_client.envoyer_relance(
            facture_id=solde.facture_id,
            abonne_id=solde.abonne_id,
            etape=4,
        )
        if not prevenu:
            logger.error(
                "Abonné suspendu SANS avoir été prévenu — le message n'est pas parti",
                extra={"facture_id": solde.facture_id, "abonne_id": solde.abonne_id},
            )
        # EF-NOTIF-005 — Notifier les admins de chaque suspension
        notif_client.notifier_admins(
            evenement="SUSPENSION",
            detail=(
                f"Abonné {solde.abonne_id} suspendu pour impayé (facture {solde.facture_id})"
                + ("" if prevenu else " — ⚠️ L'ABONNÉ N'A PAS PU ÊTRE PRÉVENU")
            ),
            entite_id=solde.abonne_id,
        )
        suivi.suspension_effectuee = True
        suivi.date_suspension = timezone.now()
        suivi.etape_actuelle = max(suivi.etape_actuelle, 4)
        logger.info("Suspension effectuée — facture %s, abonné %s", solde.facture_id, solde.abonne_id)
        return True
