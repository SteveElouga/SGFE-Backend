"""Implémentation du serveur gRPC du Paiement Service."""

import logging
import sys
from datetime import date
from decimal import Decimal
from pathlib import Path

import grpc
from django.conf import settings
from django.core.exceptions import ObjectDoesNotExist
from django.utils import timezone

# Assure que le dossier proto/ est dans sys.path avant les imports générés
sys.path.insert(0, str(Path(settings.BASE_DIR) / "proto"))

import paiement_service_pb2 as pb
import paiement_service_pb2_grpc as pb_grpc

from paiements.event_publisher import publish_paiement_event, publish_reporting_event
from paiements.grpc_clients import FacturationServiceClient, NotificationServiceClient
from paiements.grpc_interceptors import ErrorHandlingInterceptor, IdentityInterceptor
from paiements.grpc_auth import AuthServerInterceptor, ouvrir_port_grpc
from paiements.models import ModePaiement, Paiement, SoldeFacture, StatutSessionPaiement, StatutSolde
from paiements.passerelle_paiement import MockPasserellePaiementClient, PasserellePaiementClient
from paiements.serializers import (
    avoir_to_proto,
    paiement_to_proto,
    session_paiement_to_proto,
    solde_to_proto,
    suivi_to_proto,
)
from paiements.services import PaiementService

# Identifiant explicite d'un encaissement déclenché par le mock de paiement en
# ligne, jamais par un agent humain — distinct de tout `enregistre_par`
# alimenté ailleurs par un id utilisateur Auth Service.
SYSTEME_PAIEMENT_EN_LIGNE = "SYSTEME_PAIEMENT_EN_LIGNE"

logger = logging.getLogger(__name__)


class PaiementServicer(pb_grpc.PaiementServiceServicer):  # type: ignore[misc]
    # ^ PaiementServiceServicer vient du stub généré paiement_service_pb2_grpc,
    # exclu de la vérification mypy (voir mypy.ini) — mypy le voit donc comme
    # `Any`, ce qui rend toute sous-classe de lui structurellement "misc" ;
    # rien à corriger côté code métier ici.
    """Implémentation de tous les RPCs du PaiementService.

    Les exceptions (ValidationError, ObjectDoesNotExist, ValueError) ne sont pas
    interceptées ici : ErrorHandlingInterceptor s'en charge de façon centralisée
    (voir grpc_interceptors.py).
    """

    def __init__(self) -> None:
        self._svc = PaiementService()
        self._facturation_client = FacturationServiceClient()
        self._notification_client = NotificationServiceClient()

    def InitialiserSolde(
        self,
        request: pb.InitialiserSoldeRequest,
        context: grpc.ServicerContext,
    ) -> pb.SoldeResponse:
        """Initialise le solde d'une facture nouvellement générée."""
        date_limite = date.fromisoformat(request.date_limite_paiement)
        solde = self._svc.initialiser_solde(
            facture_id=request.facture_id,
            abonne_id=request.abonne_id,
            montant_total=request.montant_total,
            date_limite_paiement=date_limite,
            campagne_id=request.campagne_id,
        )

        # Un avoir disponible (trop-perçu antérieur) s'impute automatiquement
        # sur toute facture nouvellement créée (`_appliquer_avoir`, dans la
        # même transaction qu'`initialiser_solde`) — sans que rien de cela
        # n'atteigne Facturation, Reporting, ou le rétablissement d'un abonné
        # suspendu. Une facture déjà soldée par avoir restait donc affichée
        # IMPAYÉE côté Facturation pour toujours, et un abonné suspendu dont
        # l'avoir éteignait la dette totale n'était jamais rétabli — même
        # défaut que celui déjà corrigé côté encaissement, ici pour le seul
        # autre chemin qui fait varier `montant_paye` d'un solde tout neuf.
        if solde.montant_paye > 0:
            try:
                self._facturation_client.update_statut_facture(
                    facture_id=solde.facture_id,
                    statut=solde.statut,
                )
            except Exception as exc:
                logger.warning(
                    "Sync statut facture (avoir imputé à la création) échouée — dégradation gracieuse",
                    extra={"facture_id": solde.facture_id, "error": str(exc)},
                )

            # Le trop-perçu d'origine n'a jamais été compté en recette : voir
            # `_propager_versement`, étape 3 (« sera compté à nouveau quand
            # l'avoir s'imputera, le mode AVOIR crée une seconde écriture »).
            # C'est cette seconde écriture, et jusqu'ici rien ne la comptait.
            publish_reporting_event(
                "PAIEMENT_STATS",
                campagne_id=solde.campagne_id,
                montant_paiement=float(solde.montant_paye),
                type_update="PAIEMENT",
            )
            if solde.statut == StatutSolde.PAYEE:
                publish_reporting_event(
                    "PAIEMENT_STATS",
                    campagne_id=solde.campagne_id,
                    montant_paiement=0.0,
                    type_update="IMPAYE_RESOLU",
                )
                self._svc.retablir_si_dette_eteinte(solde.abonne_id, solde.facture_id)

        return solde_to_proto(solde)

    def _propager_versement(self, imputations: list[tuple[Paiement, SoldeFacture]], montant_recu: float) -> None:
        """Applique à TOUT un versement les conséquences de l'encaissement.

        ── Pourquoi cette méthode existe ────────────────────────────────────────

        Deux RPC encaissent : `EnregistrerPaiement` (le caissier vise une facture)
        et `EnregistrerPaiementAbonne` (il saisit un montant, le système
        répartit). La documentation du second le désigne comme **« le geste
        courant »**, et c'est celui que l'interface emploie.

        Le premier portait ses sept effets aval en propre. Le second appelait la
        couche métier et **retournait**. Aucun appel. Conséquences, sur le geste
        le plus fréquent de l'exploitation :

          • l'abonné suspendu qui venait payer restait coupé ;
          • la facture restait affichée IMPAYÉE après avoir été réglée ;
          • aucun reçu ne partait, alors que tout le mécanisme existe ;
          • les relances continuaient malgré l'acompte, et la suspension tombait
            quand même à J+10 ;
          • le montant encaissé du tableau de bord ignorait ces recettes.

        Un seul chemin porte désormais ces effets, pour les deux RPC. Un
        troisième point d'entrée n'a plus qu'une chose à faire : appeler ceci.

        ── Par facture, et non par versement ────────────────────────────────────

        Les effets s'appliquent à **chaque** écriture du versement, cascade
        comprise. Une vieille facture éteinte par le débordement d'un versement
        restait sinon IMPAYÉE côté Facturation, avec un `SuiviImpaye` jamais
        résolu — deux écrans du même outil en désaccord sur qui doit quoi.

        Exception : ce qui ne vaut qu'une fois par versement ne se répète pas —
        le reçu, et le rétablissement de l'abonné.
        """
        if not imputations:
            return

        abonne_id = imputations[0][0].abonne_id

        for paiement, solde in imputations:
            # 1) Statut de la facture vers Facturation Service (dégradation gracieuse)
            try:
                self._facturation_client.update_statut_facture(
                    facture_id=solde.facture_id,
                    statut=solde.statut,
                )
            except Exception as exc:
                logger.warning(
                    "Sync statut facture échouée — dégradation gracieuse",
                    extra={"facture_id": solde.facture_id, "error": str(exc)},
                )

            # 2) Résolution du suivi d'impayé, ou pause des relances
            self._svc.marquer_facture_payee_si_applicable(solde)
            self._svc.suspendre_relances_si_partiel(solde)

            # 3) Statistiques Reporting (read model aval, événementiel — ADR-019).
            #
            # Le montant publié est la part imputée à CETTE facture, et la
            # campagne est la sienne. Publier le montant reçu en entier sur la
            # campagne de la première facture attribuait toute la recette d'un
            # versement à une seule campagne — et comptait deux fois l'excédent,
            # qui sera compté à nouveau quand l'avoir s'imputera (le mode AVOIR
            # crée une seconde écriture).
            publish_reporting_event(
                "PAIEMENT_STATS",
                campagne_id=solde.campagne_id,
                montant_paiement=float(paiement.montant),
                type_update="PAIEMENT",
            )
            if solde.statut == StatutSolde.PAYEE:
                publish_reporting_event(
                    "PAIEMENT_STATS",
                    campagne_id=solde.campagne_id,
                    montant_paiement=0.0,
                    type_update="IMPAYE_RESOLU",
                )

            # 4) Souscription GraphQL `paiementCree` — une écriture, un événement.
            publish_paiement_event(paiement, statut_facture=solde.statut)

        # ── Une fois par versement ──────────────────────────────────────────
        #
        # Rétablissement d'abord : il tranche si l'abonné est à jour, et le reçu
        # a besoin de cette réponse.
        premier = imputations[0][0]
        self._svc.retablir_si_dette_eteinte(abonne_id, premier.facture_id)

        # Le reçu couvre le VERSEMENT, pas une de ses lignes.
        #
        # `montant` est ce que l'abonné a tendu, excédent compris — c'est ce qu'il
        # attend de lire sur son reçu, et la somme des parts imputées ne le dirait
        # pas quand une partie est partie en avoir.
        #
        # `solde_restant` est ce qu'il doit ENCORE EN TOUT, et non le reste d'une
        # facture parmi trois : un versement au comptoir n'a pas de « reste » qui
        # veuille dire quelque chose au niveau d'une ligne. C'est la même décision
        # que celle prise côté frontend en #95 — dire ce que l'abonné doit
        # ailleurs, plutôt que de le taire.
        self._notification_client.envoyer_recu(
            paiement_id=str(premier.id),
            facture_id=premier.facture_id,
            abonne_id=abonne_id,
            montant=float(montant_recu),
            solde_restant=float(self._svc.total_du_abonne(abonne_id)),
        )

    def EnregistrerPaiement(
        self,
        request: pb.EnregistrerPaiementRequest,
        context: grpc.ServicerContext,
    ) -> pb.PaiementResponse:
        """Enregistre un versement et met à jour le solde de la facture."""
        date_paiement = date.fromisoformat(request.date_paiement)
        paiement, solde = self._svc.enregistrer_paiement(
            facture_id=request.facture_id,
            abonne_id=request.abonne_id,
            montant=request.montant,
            date_paiement=date_paiement,
            mode_paiement=request.mode_paiement,
            reference_transaction=request.reference_transaction,
            enregistre_par=request.enregistre_par,
        )

        # Toutes les factures touchées, pas seulement celle que le caissier a
        # visée : un versement qui dépasse déborde sur les impayés, et ces
        # factures-là ont les mêmes conséquences à porter.
        self._propager_versement(
            self._svc.imputations_du_versement(paiement.versement_id),
            montant_recu=request.montant,
        )

        return paiement_to_proto(paiement)

    def AnnulerPaiement(
        self,
        request: pb.AnnulerPaiementRequest,
        context: grpc.ServicerContext,
    ) -> pb.PaiementResponse:
        """Annule un paiement enregistré par erreur et rétablit le solde de la facture."""
        paiement, solde = self._svc.annuler_paiement(
            paiement_id=request.paiement_id,
            motif=request.motif,
            annule_par=request.annule_par,
        )

        # Un versement s'impute sur la facture visée, puis en cascade sur les
        # impayés : `annuler_paiement` défait CHAQUE écriture qui en est née,
        # mais ne rend que celle demandée. Sans cette relecture, les autres
        # factures touchées restaient PAYÉE pour toujours côté Facturation, et
        # leurs recettes jamais décrémentées côté Reporting — même défaut que
        # celui déjà corrigé côté encaissement (voir `_propager_versement`).
        # `imputations_du_versement` rend les soldes déjà relus après
        # l'annulation : ils portent donc son statut définitif.
        for ecriture, solde_ecriture in self._svc.imputations_du_versement(paiement.versement_id):
            # Synchronise le statut de facture rétabli vers Facturation (dégradation gracieuse)
            try:
                self._facturation_client.update_statut_facture(
                    facture_id=solde_ecriture.facture_id,
                    statut=solde_ecriture.statut,
                )
            except Exception as exc:
                logger.warning(
                    "Sync statut facture (annulation) échouée — dégradation gracieuse",
                    extra={"facture_id": solde_ecriture.facture_id, "error": str(exc)},
                )

            # Décrémente les recettes du read model Reporting.
            #
            # L'enregistrement d'un versement publiait `PAIEMENT` ; son annulation ne
            # publiait rien. Le read model surévaluait donc les recettes de façon
            # permanente, en désaccord avec `statsParMois` que la gateway calcule en
            # excluant les paiements annulés.
            publish_reporting_event(
                "PAIEMENT_STATS",
                campagne_id=solde_ecriture.campagne_id,
                montant_paiement=float(ecriture.montant),
                type_update="PAIEMENT_ANNULE",
            )

            # Notifie la gateway pour que les écrans ouverts se rafraîchissent : un
            # solde qui remonte doit se voir sans recharger la page, comme un
            # versement se voit.
            publish_paiement_event(ecriture, statut_facture=solde_ecriture.statut)

            # Prévient l'abonné (dégradation gracieuse assurée par le client).
            #
            # Il détenait un reçu et croyait cette facture soldée. Se taire le
            # laisse découvrir la chose à la relance suivante — et le message
            # d'étape 5 dit ce qui reste dû, plus qu'il ne rappelle ce qui a
            # été défait.
            self._notification_client.envoyer_relance(
                facture_id=ecriture.facture_id,
                abonne_id=ecriture.abonne_id,
                etape=5,
            )

        return paiement_to_proto(paiement)

    def CrediterAvoir(
        self,
        request: pb.CrediterAvoirRequest,
        context: grpc.ServicerContext,
    ) -> pb.AvoirResponse:
        """Émet un avoir manuel (note de rectification) sur le compte de l'abonné."""
        self._svc.crediter_avoir_manuel(
            abonne_id=request.abonne_id,
            montant=request.montant,
            motif=request.motif,
            cree_par=request.cree_par,
        )
        montant, mouvements = self._svc.get_avoir_abonne(request.abonne_id)
        return avoir_to_proto(request.abonne_id, montant, mouvements)

    def GetAvoirAbonne(
        self,
        request: pb.AbonneIdRequest,
        context: grpc.ServicerContext,
    ) -> pb.AvoirResponse:
        """Retourne le solde d'avoir + le journal des mouvements d'un abonné."""
        montant, mouvements = self._svc.get_avoir_abonne(request.abonne_id)
        return avoir_to_proto(request.abonne_id, montant, mouvements)

    def AnnulerSolde(
        self,
        request: pb.AnnulerSoldeRequest,
        context: grpc.ServicerContext,
    ) -> pb.AnnulerSoldeResponse:
        """Éteint le solde d'une facture annulée, en rendant l'argent déjà versé.

        Idempotent : réannuler renvoie le solde tel quel avec un report nul,
        plutôt que de créditer l'abonné une seconde fois.
        """
        solde, porte_en_avoir = self._svc.annuler_solde(facture_id=request.facture_id, motif=request.motif)

        # Ce qui avait déjà été versé sur cette facture était compté en recette
        # (événement `PAIEMENT`, publié à l'encaissement). L'annulation le porte
        # à l'avoir de l'abonné — où il sera compté À NOUVEAU quand il s'imputera
        # sur une facture future (voir `InitialiserSolde`) — mais rien ne
        # décrémentait la recette d'origine : Reporting surévaluait donc les
        # encaissements de cette campagne pour toujours, en double emploi avec
        # le futur re-comptage. Même correction que `AnnulerPaiement`, pour
        # l'autre façon dont une facture cesse d'être due.
        if porte_en_avoir > 0:
            publish_reporting_event(
                "PAIEMENT_STATS",
                campagne_id=solde.campagne_id,
                montant_paiement=float(porte_en_avoir),
                type_update="PAIEMENT_ANNULE",
            )

        # Prévient l'abonné dans tous les cas (dégradation gracieuse assurée
        # par le client, comme dans `AnnulerPaiement`) — pas seulement quand de
        # l'argent était en jeu. Une facture annulée alors qu'elle était encore
        # entièrement impayée ne déclenchait rien : l'abonné, PDF déjà reçu,
        # continuait de croire cette facture due. Étape 5 (un versement existait
        # et devient un avoir) et étape 6 (aucun argent n'a changé de main)
        # racontent deux histoires différentes — le motif « versement annulé »
        # de l'étape 5 serait faux pour quelqu'un qui n'a jamais payé.
        self._notification_client.envoyer_relance(
            facture_id=solde.facture_id,
            abonne_id=solde.abonne_id,
            etape=5 if porte_en_avoir > 0 else 6,
        )

        return pb.AnnulerSoldeResponse(
            solde=solde_to_proto(solde),
            montant_porte_en_avoir=float(porte_en_avoir),
        )

    def GetSolde(
        self,
        request: pb.FactureIdRequest,
        context: grpc.ServicerContext,
    ) -> pb.SoldeResponse:
        """Retourne le solde courant d'une facture."""
        solde = self._svc.get_solde(request.facture_id)
        return solde_to_proto(solde)

    def ListPaiements(
        self,
        request: pb.ListPaiementsRequest,
        context: grpc.ServicerContext,
    ) -> pb.ListPaiementsResponse:
        """Liste les paiements filtrés par facture et/ou abonné."""
        limit = request.limit if request.HasField("limit") else None
        offset = request.offset if request.HasField("offset") else None
        paiements = self._svc.list_paiements(
            facture_id=request.facture_id,
            abonne_id=request.abonne_id,
            date_debut=request.date_debut,
            date_fin=request.date_fin,
            limit=limit,
            offset=offset,
        )
        total = self._svc.count_paiements(
            facture_id=request.facture_id,
            abonne_id=request.abonne_id,
            date_debut=request.date_debut,
            date_fin=request.date_fin,
        )
        return pb.ListPaiementsResponse(paiements=[paiement_to_proto(p) for p in paiements], total=total)

    def ListPaiementsParCampagne(
        self,
        request: pb.CampagneIdRequest,
        context: grpc.ServicerContext,
    ) -> pb.ListPaiementsResponse:
        """Liste les paiements de toutes les factures d'une campagne (export CSV)."""
        paiements = self._svc.list_paiements_par_campagne(campagne_id=request.campagne_id)
        return pb.ListPaiementsResponse(paiements=[paiement_to_proto(p) for p in paiements], total=len(paiements))

    def ListImpayes(
        self,
        request: pb.EmptyRequest,
        context: grpc.ServicerContext,
    ) -> pb.ListImpayesResponse:
        """Retourne toutes les factures impayées dont la date limite est dépassée."""
        impayes = self._svc.list_impayes()
        return pb.ListImpayesResponse(impayes=[solde_to_proto(s) for s in impayes])

    def GetSuiviImpaye(
        self,
        request: pb.FactureIdRequest,
        context: grpc.ServicerContext,
    ) -> pb.SuiviImpayeResponse:
        """Retourne le suivi d'impayé pour une facture."""
        suivi = self._svc.get_suivi_impaye(request.facture_id)
        return suivi_to_proto(suivi)

    def GetDetteAbonne(
        self,
        request: pb.DetteAbonneRequest,
        context: grpc.ServicerContext,
    ) -> pb.DetteAbonneResponse:
        """Ce qu'un abonné doit encore, toutes factures confondues.

        `hors_facture_id` sert à l'impression : sur une facture, le « solde
        antérieur » est ce qu'il doit EN PLUS de celle qu'il tient en main.
        """
        soldes = [
            s
            for s in self._svc.list_non_soldes_par_abonne(request.abonne_id)
            if not request.hors_facture_id or s.facture_id != request.hors_facture_id
        ]
        total = self._svc.total_du_abonne(request.abonne_id, request.hors_facture_id)
        # La plus ancienne échéance dit l'âge de la dette — c'est elle qui fait
        # payer, pas le montant.
        plus_ancienne = min((s.date_limite_paiement for s in soldes), default=None)
        return pb.DetteAbonneResponse(
            total_du=float(total),
            nb_factures=len(soldes),
            plus_ancienne_echeance=plus_ancienne.isoformat() if plus_ancienne else "",
        )

    def _encaisser_pour_abonne(
        self,
        abonne_id: str,
        montant: float,
        date_paiement: date,
        mode_paiement: str,
        reference_transaction: str,
        enregistre_par: str,
    ) -> tuple[list[Paiement], Decimal]:
        """Encaisse un versement pour un abonné et en propage TOUTES les conséquences.

        Factorise ce que `EnregistrerPaiementAbonne` (le comptoir) et
        `ConfirmerSessionPaiementEnLigne` (le paiement en ligne, sandbox/mock)
        ont en commun : la même règle d'imputation
        (`enregistrer_paiement_abonne`, du plus ancien au plus récent), suivie
        des mêmes effets aval (`_propager_versement`). Ne PAS dupliquer cet
        appel ailleurs — c'est tout l'intérêt de `_propager_versement`, voir
        sa docstring.
        """
        paiements, excedent = self._svc.enregistrer_paiement_abonne(
            abonne_id=abonne_id,
            montant=montant,
            date_paiement=date_paiement,
            mode_paiement=mode_paiement,
            reference_transaction=reference_transaction,
            enregistre_par=enregistre_par,
        )

        # `paiements` peut être vide : un abonné qui ne doit rien et qui verse
        # quand même voit tout son versement partir en avoir, sans écriture
        # d'imputation. Il n'y a alors rien à propager — mais l'avoir est bien
        # crédité par la couche métier.
        if paiements:
            self._propager_versement(
                self._svc.imputations_du_versement(paiements[0].versement_id),
                montant_recu=montant,
            )

        return paiements, excedent

    def EnregistrerPaiementAbonne(
        self,
        request: pb.EnregistrerPaiementAbonneRequest,
        context: grpc.ServicerContext,
    ) -> pb.PaiementAbonneResponse:
        """Encaisse un versement imputé du plus ancien au plus récent.

        C'est le geste courant du comptoir, et c'est celui que l'interface
        emploie (`encaissement-sheet`). Il n'avait AUCUN effet aval : ni statut de
        facture, ni réactivation de l'abonné suspendu, ni reçu, ni pause des
        relances, ni statistiques. Voir `_propager_versement`.
        """
        paiements, excedent = self._encaisser_pour_abonne(
            abonne_id=request.abonne_id,
            montant=request.montant,
            date_paiement=date.fromisoformat(request.date_paiement),
            mode_paiement=request.mode_paiement,
            reference_transaction=request.reference_transaction,
            enregistre_par=request.enregistre_par,
        )

        return pb.PaiementAbonneResponse(
            paiements=[paiement_to_proto(p) for p in paiements],
            excedent_en_avoir=float(excedent),
        )

    def CreerSessionPaiementEnLigne(
        self,
        request: pb.CreerSessionPaiementRequest,
        context: grpc.ServicerContext,
    ) -> pb.SessionPaiementResponse:
        """Ouvre une session de paiement en ligne (mock) pour l'espace abonné public.

        **Mode sandbox/mock exclusivement** — voir `passerelle_paiement.py`.
        Aucune vraie passerelle n'est contactée : `url_redirection` pointe
        vers le mock de confirmation côté frontend.

        `token_espace` est revalidé ICI, indépendamment de la gateway :
        l'abonné propriétaire de la session est celui du token, jamais un
        champ transmis par l'appelant — même défense anti-IDOR qu'ANO-002
        pour le reste de l'espace abonné.
        """
        token_resp = self._notification_client.valider_token(request.token_espace)
        if not token_resp["is_valid"]:
            raise ObjectDoesNotExist("Token de l'espace abonné invalide ou expiré.")

        session = self._svc.creer_session_paiement_en_ligne(
            facture_id=request.facture_id,
            montant=request.montant,
            abonne_id=token_resp["abonne_id"],
            token_espace=request.token_espace,
        )

        passerelle: PasserellePaiementClient = MockPasserellePaiementClient(request.token_espace)
        url_redirection = passerelle.creer_session(Decimal(str(request.montant)), str(session.id))

        return session_paiement_to_proto(session, url_redirection)

    def ConfirmerSessionPaiementEnLigne(
        self,
        request: pb.ConfirmerSessionPaiementRequest,
        context: grpc.ServicerContext,
    ) -> pb.SessionPaiementResponse:
        """Confirme une session de paiement en ligne (mock) et encaisse le versement.

        Anti-IDOR : exige EXACTEMENT le token qui a créé la session (voir
        `SessionPaiementEnLigne.token_espace`) — un token par ailleurs valide
        mais différent est traité comme si la session n'existait pas.

        Idempotent : une session déjà tranchée (CONFIRMEE, ECHOUEE ou
        EXPIREE) renvoie son état actuel sans rejouer l'encaissement ni la
        passerelle — la contrainte d'unicité sur `reference_transaction`
        protège de toute façon `enregistrer_paiement_abonne` d'un doublon,
        mais rejouer aurait aussi renvoyé un second reçu/une seconde
        notification pour rien.
        """
        session = self._svc.get_session_paiement(request.session_id)
        if session.token_espace != request.token_espace:
            raise ObjectDoesNotExist("Session de paiement introuvable.")

        if session.statut != StatutSessionPaiement.EN_ATTENTE:
            return session_paiement_to_proto(session)

        if self._svc.session_expiree(session):
            session = self._svc.marquer_session_statut(session, StatutSessionPaiement.EXPIREE)
            return session_paiement_to_proto(session)

        passerelle: PasserellePaiementClient = MockPasserellePaiementClient(session.token_espace)
        if not passerelle.confirmer(str(session.id)):
            session = self._svc.marquer_session_statut(session, StatutSessionPaiement.ECHOUEE)
            return session_paiement_to_proto(session)

        self._encaisser_pour_abonne(
            abonne_id=session.abonne_id,
            montant=float(session.montant),
            date_paiement=timezone.now().date(),
            mode_paiement=ModePaiement.MOBILE_MONEY,
            reference_transaction=str(session.id),
            enregistre_par=SYSTEME_PAIEMENT_EN_LIGNE,
        )

        session = self._svc.marquer_session_statut(session, StatutSessionPaiement.CONFIRMEE)
        return session_paiement_to_proto(session)


def serve() -> None:
    """Démarre le serveur gRPC (appelé par la commande de management)."""
    import concurrent.futures

    server = grpc.server(
        concurrent.futures.ThreadPoolExecutor(max_workers=10),
        interceptors=[
            AuthServerInterceptor(settings.INTERNAL_GRPC_KEY),
            ErrorHandlingInterceptor(),
            IdentityInterceptor(),
        ],
    )
    pb_grpc.add_PaiementServiceServicer_to_server(PaiementServicer(), server)
    port = getattr(settings, "PAIEMENT_GRPC_PORT", 50055)
    ouvrir_port_grpc(server, port)
    server.start()
    logger.info("Paiement gRPC server démarré sur le port %d", port)
    server.wait_for_termination()
