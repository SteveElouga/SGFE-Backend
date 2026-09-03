"""Logique métier du Campagne Service."""

import logging
from datetime import datetime
from decimal import Decimal
from typing import Any, Optional, cast

import grpc
from django.core.exceptions import ValidationError
from django.db import transaction

from .dtos import AgentAffecteDict, StatsReportingDict, ZoneAgentDict
from .grpc_clients import AbonneServiceClient, FacturationServiceClient
from .models import ActionAudit, AffectationZone, Campagne, Releve, StatutCampagne, StatutReleve
from .repositories import (
    AffectationZoneRepository,
    CampagneAgentRepository,
    CampagneRepository,
    RegenerationFactureEnAttenteRepository,
    ReleveAuditRepository,
    ReleveRepository,
)

logger = logging.getLogger(__name__)


class CampagneService:
    """Gestion des campagnes de relevé."""

    def __init__(self) -> None:
        self._repo = CampagneRepository()
        self._releve_repo = ReleveRepository()
        self._agent_repo = CampagneAgentRepository()
        self._zone_repo = AffectationZoneRepository()
        self._retry_repo = RegenerationFactureEnAttenteRepository()
        self._abonne_client = AbonneServiceClient()
        self._facturation_client = FacturationServiceClient()

    def _verifier_abonne_actif(self, abonne_id: str) -> Any:
        """Vérifie que l'abonné est ACTIF et le retourne (avec son compteur).

        Type de retour `Any` assumé : c'est un message protobuf `AbonneResponse`
        (voir `abonne_service_pb2`, stub généré exclu de la vérification mypy —
        même raison que les autres `*_pb2.py`).

        Règle métier obligatoire (CLAUDE.md racine) : un abonné suspendu ou
        résilié ne peut pas être relevé. On échoue de façon volontairement
        bloquante (pas de dégradation gracieuse) si Abonné Service est
        inaccessible : c'est une validation métier obligatoire, pas un enrichissement
        optionnel — il vaut mieux refuser l'opération que de la laisser
        passer sans avoir pu vérifier le statut.
        """
        try:
            abonne = self._abonne_client.get_abonne(abonne_id)
        except grpc.RpcError as exc:
            raise ValidationError(
                f"Impossible de vérifier le statut de l'abonné {abonne_id} "
                f"(Abonné Service inaccessible) : {exc.details() if hasattr(exc, 'details') else exc}"
            ) from exc
        if abonne.statut != "ACTIF":
            raise ValidationError(
                f"L'abonné {abonne_id} n'est pas ACTIF (statut actuel : {abonne.statut}). "
                "Un abonné suspendu ou résilié ne peut pas être relevé."
            )
        return abonne

    @staticmethod
    def _zone_de(abonne: Any) -> tuple[str, Optional[int]]:
        """Extrait la zone (quartier, camp) du compteur d'un abonné, à copier
        dans le relevé. Tolère l'absence de compteur (retourne '', None)."""
        compteur = getattr(abonne, "compteur", None)
        if compteur is None:
            return "", None
        quartier = getattr(compteur, "quartier", "") or ""
        camp = getattr(compteur, "camp", None)
        return quartier, camp

    def creer_campagne(
        self,
        nom: str,
        periode_mois: int,
        periode_annee: int,
        created_by: str,
        date_planifiee: Optional[str] = None,
        numero_mobile_money: str = "",
        generer_factures_auto: bool = True,
        envoyer_whatsapp_auto: bool = True,
        demarrer_maintenant: bool = False,
    ) -> Campagne:
        if not nom.strip():
            raise ValidationError("Le nom de la campagne est obligatoire.")
        if not (1 <= periode_mois <= 12):
            raise ValidationError("Le mois de la période doit être entre 1 et 12.")
        if periode_annee < 2000:
            raise ValidationError("L'année est invalide.")
        if not created_by:
            raise ValidationError("L'identifiant du créateur est obligatoire.")
        if numero_mobile_money and (not numero_mobile_money.isdigit() or len(numero_mobile_money) != 9):
            raise ValidationError("Le numéro Mobile Money doit contenir exactement 9 chiffres (ex: 658552294).")
        return self._repo.create(
            nom=nom,
            periode_mois=periode_mois,
            periode_annee=periode_annee,
            created_by=created_by,
            date_planifiee=date_planifiee,
            numero_mobile_money=numero_mobile_money,
            generer_factures_auto=generer_factures_auto,
            envoyer_whatsapp_auto=envoyer_whatsapp_auto,
            demarrer_maintenant=demarrer_maintenant,
        )

    def demarrer_campagne(self, campagne_id: str) -> Campagne:
        campagne = self._repo.get_by_id(campagne_id)
        if campagne.statut != StatutCampagne.PLANIFIEE:
            raise ValidationError(f"Seule une campagne PLANIFIEE peut être démarrée. Statut actuel : {campagne.statut}")
        return self._repo.update_statut(campagne, StatutCampagne.EN_COURS)

    def cloturer_campagne(self, campagne_id: str) -> Campagne:
        campagne = self._repo.get_by_id(campagne_id)
        if campagne.statut != StatutCampagne.EN_COURS:
            raise ValidationError(f"Seule une campagne EN_COURS peut être clôturée. Statut actuel : {campagne.statut}")
        return self._repo.update_statut(campagne, StatutCampagne.CLOTUREE)

    def get_campagne(self, campagne_id: str) -> Campagne:
        return self._repo.get_by_id(campagne_id)

    def list_campagnes(self, created_by: str = "", agent_id: str = "") -> list[Campagne]:
        return self._repo.list_all(created_by=created_by, agent_id=agent_id)

    def get_progression(self, campagne_id: str) -> dict[str, int]:
        """Retourne le nombre de relevés par statut pour la campagne."""
        self._repo.get_by_id(campagne_id)  # lève ObjectDoesNotExist si introuvable
        return self._releve_repo.count_by_campagne(campagne_id)

    def get_resume_cloture(self, campagne_id: str) -> dict[str, int]:
        """Aperçu prêt à afficher avant la clôture : ventilation des relevés par
        statut et nombre de factures qui seront générées (relevés + estimés ;
        les non-relevés et restants ne sont pas facturés)."""
        self._repo.get_by_id(campagne_id)  # lève ObjectDoesNotExist si introuvable
        counts = self._releve_repo.count_by_campagne(campagne_id)
        nb_releves = counts.get(StatutReleve.RELEVE, 0)
        nb_estimes = counts.get(StatutReleve.ESTIME, 0)
        nb_non_releves = counts.get(StatutReleve.NON_RELEVE, 0)
        nb_restants = counts.get(StatutReleve.A_RELEVER, 0)
        return {
            "total_abonnes": nb_releves + nb_estimes + nb_non_releves + nb_restants,
            "nb_releves": nb_releves,
            "nb_estimes": nb_estimes,
            "nb_non_releves": nb_non_releves,
            "nb_restants": nb_restants,
            "nb_factures_a_generer": nb_releves + nb_estimes,
        }

    def get_stats_reporting(self, campagne_id: str) -> StatsReportingDict:
        """Stats poussées au Reporting Service à la clôture (nom, total, relevés, consommation)."""
        campagne = self._repo.get_by_id(campagne_id)
        counts = self._releve_repo.count_by_campagne(campagne_id)
        return {
            "nom_campagne": campagne.nom,
            "total_abonnes": sum(counts.values()),
            "nb_releves": counts.get(StatutReleve.RELEVE, 0),
            "consommation_totale": self._releve_repo.sum_consommation_by_campagne(campagne_id),
        }

    def ajouter_abonne_campagne(
        self,
        campagne_id: str,
        abonne_id: str,
        ancien_index: Decimal,
    ) -> Releve:
        campagne = self._repo.get_by_id(campagne_id)
        if campagne.statut not in (StatutCampagne.PLANIFIEE, StatutCampagne.EN_COURS):
            raise ValidationError("Impossible d'ajouter un abonné à une campagne clôturée.")
        abonne = self._verifier_abonne_actif(abonne_id)
        existant = self._releve_repo.get_by_campagne_abonne(campagne_id, abonne_id)
        if existant:
            raise ValidationError(f"L'abonné {abonne_id} est déjà inscrit à la campagne {campagne_id}.")
        quartier, camp = self._zone_de(abonne)
        return self._releve_repo.create(
            campagne=campagne,
            abonne_id=abonne_id,
            ancien_index=ancien_index,
            quartier=quartier,
            camp=camp,
        )

    def verifier_deja_presente(self, campagne_id: str) -> Optional[Campagne]:
        """Retourne la première campagne EN_COURS, ou None."""
        en_cours = self._repo.list_en_cours()
        return en_cours[0] if en_cours else None

    def demarrer_campagnes_planifiees_pour_aujourd_hui(self) -> list[Campagne]:
        """Cron 7h00 : démarre TOUTES les campagnes planifiées pour aujourd'hui ou J-1.

        Avant ANO-019, seule la première campagne PLANIFIEE trouvée pour une
        date donnée démarrait (.first()) — les autres campagnes partageant la
        même date_planifiee restaient bloquées indéfiniment sans alerte.
        """
        from datetime import date, timedelta

        demarrees: list[Campagne] = []
        for delta in (0, -1):
            cible = date.today() + timedelta(days=delta)
            for campagne in self._repo.list_planifiees_pour_date(cible):
                try:
                    updated = self._repo.update_statut(campagne, StatutCampagne.EN_COURS)
                    demarrees.append(updated)
                except Exception:
                    # Ne pas bloquer les autres campagnes du lot, mais ne plus
                    # avaler l'échec silencieusement (diagnostic du cron 7h).
                    logger.exception(
                        "Échec du démarrage automatique de la campagne %s (%s)",
                        campagne.id,
                        campagne.nom,
                    )
        return demarrees

    def affecter_zones(
        self,
        campagne_id: str,
        agent_id: str,
        zones: list[tuple[str, int]],
    ) -> list[AgentAffecteDict]:
        """Affecte un agent à un ensemble de zones (remplace ses zones actuelles).

        Affecter des zones implique que l'agent travaille la campagne : on
        garantit aussi son affectation globale (``CampagneAgent``) pour qu'il
        puisse saisir. Retourne la liste des agents rafraîchie.
        """
        campagne = self._repo.get_by_id(campagne_id)
        if not agent_id:
            raise ValidationError("L'identifiant de l'agent est obligatoire.")
        with transaction.atomic():
            self._agent_repo.assigner(campagne, agent_id)
            self._zone_repo.set_zones_for_agent(campagne, agent_id, zones)
        return self.list_agents_campagne(campagne_id)

    def list_agents_campagne(self, campagne_id: str) -> list[AgentAffecteDict]:
        """Agents affectés à une campagne (global et/ou par zone), avec stats.

        Pour chaque agent : ses zones (avec le nb de relevés RELEVE de la zone),
        son total de relevés saisis et la date de son dernier relevé. Le nombre
        d'abonnés par zone (dénominateur) est ajouté côté Gateway via
        ListZones (Abonné Service) — non requis ici.
        """
        from collections import defaultdict

        self._repo.get_by_id(campagne_id)  # lève ObjectDoesNotExist si introuvable
        global_ids = self._agent_repo.list_agent_ids(campagne_id)
        zones = self._zone_repo.list_by_campagne(campagne_id)
        zone_counts = self._releve_repo.count_releves_by_zone(campagne_id)
        agent_stats = self._releve_repo.stats_by_agent(campagne_id)

        zones_by_agent: dict[str, list[AffectationZone]] = defaultdict(list)
        for z in zones:
            zones_by_agent[z.agent_id].append(z)

        # Union ordonnée : agents affectés globalement puis agents ayant des zones.
        agent_ids = list(dict.fromkeys(global_ids + list(zones_by_agent.keys())))

        agents: list[AgentAffecteDict] = []
        for agent_id in agent_ids:
            stats = agent_stats.get(agent_id, {})
            zones_dict: list[ZoneAgentDict] = [
                {
                    "quartier": z.quartier,
                    "camp": z.camp,
                    "nb_releves": zone_counts.get((z.quartier, z.camp), 0),
                }
                for z in zones_by_agent.get(agent_id, [])
            ]
            agents.append(
                {
                    "agent_id": agent_id,
                    "zones": zones_dict,
                    "nb_releves": cast(int, stats.get("nb_releves", 0)),
                    "derniere_activite": cast(Optional[datetime], stats.get("derniere_activite")),
                }
            )
        return agents

    # ------------------------------------------------------------------ #
    # Retry facturation (clôture → Facturation Service injoignable)
    # ------------------------------------------------------------------ #

    def retenter_facturation_en_attente(self) -> list[Campagne]:
        """Retente la notification de clôture pour les campagnes dont
        Facturation Service était injoignable au moment de `CloturerCampagne`.

        Rejoue le MÊME appel gRPC (`notifier_campagne_cloturee`, qui déclenche
        `GenererFactures` côté Facturation Service) que la clôture — aucune
        logique de génération dupliquée ici, seulement son déclenchement.

        Retourne les campagnes dont la notification a enfin réussi (marqueur
        `facturation_en_attente` retiré).
        """
        resolues: list[Campagne] = []
        for campagne in self._repo.list_facturation_en_attente():
            ok = self._facturation_client.notifier_campagne_cloturee(
                str(campagne.id),
                numero_mobile_money=campagne.numero_mobile_money,
                envoyer_whatsapp_auto=campagne.envoyer_whatsapp_auto,
            )
            if ok:
                self._repo.marquer_facturation_en_attente(campagne, False)
                resolues.append(campagne)
        return resolues

    # ------------------------------------------------------------------ #
    # Retry régénération de facture (correction de relevé après facturation)
    # ------------------------------------------------------------------ #

    def regenerer_facture_si_necessaire(
        self,
        campagne_id: str,
        abonne_id: str,
        motif: str,
        demande_par: str = "",
    ) -> bool:
        """Régénère la facture d'un abonné si une facture existe déjà pour cette campagne.

        Appelé après une correction de relevé postérieure à la facturation
        (`CorrigerReleve` reste autorisé sur une campagne CLOTUREE). Ne fait
        rien si aucune facture n'existe encore pour cet abonné dans cette
        campagne : la correction a précédé la clôture, ce n'est pas une erreur.

        En cas d'échec ou d'indisponibilité de Facturation Service, programme
        un retry (`RegenerationFactureEnAttente`) plutôt que d'avaler
        l'échec : la correction du relevé, déjà validée en base par
        l'appelant, n'est elle jamais perdue — seule sa répercussion sur la
        facture est différée.

        Retourne True si résolu (rien à répercuter, ou régénération réussie),
        False si Facturation Service est resté inaccessible (retry programmé).
        """
        campagne = self._repo.get_by_id(campagne_id)
        try:
            facture_id = self._facturation_client.get_facture_active(campagne_id, abonne_id)
        except Exception as exc:
            # Exception large et non seulement grpc.RpcError : cet appel est
            # best-effort après une correction déjà validée en base — une
            # erreur inattendue ici ne doit jamais remonter jusqu'à
            # l'interceptor et faire échouer la réponse gRPC de CorrigerReleve,
            # ce qui ferait croire au client que sa correction a été perdue.
            logger.error(
                "Facturation Service inaccessible — impossible de savoir si une facture existe déjà pour "
                "cet abonné après correction du relevé. Nouvelle tentative programmée.",
                extra={"campagne_id": campagne_id, "abonne_id": abonne_id, "error": str(exc)},
            )
            self._retry_repo.upsert(campagne, abonne_id, motif=motif, demande_par=demande_par)
            return False

        if facture_id is None:
            # Aucune facture émise pour cet abonné dans cette campagne :
            # correction antérieure à la clôture, rien à répercuter. Nettoie
            # un éventuel retry devenu obsolète (ex. la facture visée par une
            # tentative précédente a depuis été annulée sans remplacement).
            self._retry_repo.supprimer(campagne_id, abonne_id)
            return True

        ok = self._facturation_client.regenerer_facture(facture_id, motif=motif, regenere_par=demande_par)
        if not ok:
            logger.error(
                "Facturation Service inaccessible — la facture existante n'a pas pu être régénérée après "
                "correction du relevé. Nouvelle tentative programmée.",
                extra={"campagne_id": campagne_id, "abonne_id": abonne_id, "facture_id": facture_id},
            )
            self._retry_repo.upsert(campagne, abonne_id, motif=motif, demande_par=demande_par)
            return False

        self._retry_repo.supprimer(campagne_id, abonne_id)
        return True

    def retenter_corrections_en_attente(self) -> list[tuple[str, str]]:
        """Rejoue les régénérations de facture différées faute de Facturation Service joignable.

        Retourne les paires (campagne_id, abonne_id) résolues lors de cette passe.
        """
        resolues: list[tuple[str, str]] = []
        for entree in self._retry_repo.list_all():
            self._retry_repo.marquer_tentative(entree)
            ok = self.regenerer_facture_si_necessaire(
                str(entree.campagne_id),
                entree.abonne_id,
                motif=entree.motif,
                demande_par=entree.demande_par,
            )
            if ok:
                resolues.append((str(entree.campagne_id), entree.abonne_id))
        return resolues


class ReleveService:
    """Gestion des relevés d'index."""

    def __init__(self) -> None:
        self._repo = ReleveRepository()
        self._campagne_repo = CampagneRepository()
        self._audit_repo = ReleveAuditRepository()
        self._zone_repo = AffectationZoneRepository()

    def _hors_perimetre_zone(self, releve: Releve, agent_id: str) -> bool:
        """Vrai si le relevé est hors des zones affectées à ``agent_id``.

        Un auteur SANS zone affectée n'est pas restreint : agent global, ou
        ADMIN/SUPERVISEUR (à qui l'on n'attribue jamais de zone) → couvre toute
        la campagne, comme ``list_tournee``.
        """
        if not agent_id:
            return False
        zones = {(z.quartier, z.camp) for z in self._zone_repo.list_for_agent(str(releve.campagne_id), agent_id)}
        if not zones:
            return False
        return (releve.quartier, releve.camp) not in zones

    def _verifier_perimetre_agent(self, releve: Releve, agent_id: str, auteur_role: str) -> None:
        """Refuse à un AGENT d'écrire un relevé hors de ses zones affectées.

        Cloisonnement symétrique de la tournée (``list_tournee``) : la lecture
        est déjà filtrée par zones ; sans ce contrôle en écriture, un agent
        pourrait agir sur un relevé d'une zone affectée à un autre agent. ADMIN
        et SUPERVISEUR ne sont pas restreints (aucune zone affectée).
        """
        if auteur_role == "AGENT" and self._hors_perimetre_zone(releve, agent_id):
            raise ValidationError("Ce relevé est hors de votre périmètre de zones affectées.")

    def list_tournee(self, campagne_id: str, agent_id: str) -> list[Releve]:
        """Tournée d'un agent : ce qu'il a **déjà saisi** + les abonnés **à
        relever** de son périmètre.

        Périmètre = ses **zones** affectées (quartier + camp) ; s'il n'a **aucune
        zone** (agent affecté globalement à la campagne), sa tournée couvre **tous**
        les abonnés à relever de la campagne. Sans cette logique, un écran filtrant
        sur `agent_id` ne verrait jamais les relevés A_RELEVER (dont l'agent n'est
        renseigné qu'à la saisie) — l'agent ne voyait donc rien à relever.
        """
        self._campagne_repo.get_by_id(campagne_id)  # lève ObjectDoesNotExist si introuvable
        zones = {(z.quartier, z.camp) for z in self._zone_repo.list_for_agent(campagne_id, agent_id)}
        tournee: list[Releve] = []
        for releve in self._repo.list_by_campagne(campagne_id):
            saisi_par_lui = releve.agent_id == agent_id
            a_relever_dans_perimetre = releve.statut == StatutReleve.A_RELEVER and (
                not zones or (releve.quartier, releve.camp) in zones
            )
            if saisi_par_lui or a_relever_dans_perimetre:
                tournee.append(releve)
        return tournee

    def saisir_index(
        self,
        releve_id: str,
        nouveau_index: Decimal,
        agent_id: str,
        observation: str = "",
        auteur_username: str = "",
        auteur_role: str = "",
    ) -> Releve:
        releve = self._repo.get_by_id(releve_id)
        self._verifier_perimetre_agent(releve, agent_id, auteur_role)
        if releve.campagne.statut != StatutCampagne.EN_COURS:
            raise ValidationError("Le relevé ne peut être saisi que sur une campagne EN_COURS.")
        if releve.statut == StatutReleve.RELEVE:
            raise ValidationError("Cet index a déjà été relevé.")
        if nouveau_index < releve.ancien_index:
            raise ValidationError(
                f"Le nouvel index ne peut pas être inférieur à l'ancien index ({releve.ancien_index})."
            )
        with transaction.atomic():
            releve = self._repo.saisir_index(
                releve=releve,
                nouveau_index=nouveau_index,
                agent_id=agent_id,
                observation=observation,
            )
            self._audit_repo.create(
                releve=releve,
                action=ActionAudit.SAISIE,
                auteur_id=agent_id,
                auteur_username=auteur_username,
                auteur_role=auteur_role,
                ancien_index=releve.ancien_index,
                nouvel_index=nouveau_index,
            )
        return releve

    def corriger_releve(
        self,
        releve_id: str,
        nouveau_index: Decimal,
        auteur_id: str,
        auteur_username: str = "",
        auteur_role: str = "",
        observation: str = "",
    ) -> Releve:
        """Corrige un index déjà relevé (ADMIN ou SUPERVISEUR propriétaire).

        Contrairement à ``saisir_index``, la correction est autorisée quel que
        soit le statut de la campagne (y compris CLOTUREE) : une erreur de
        saisie doit pouvoir être rectifiée après coup. Chaque correction est
        journalisée (``ReleveAudit`` action CORRECTION).
        """
        releve = self._repo.get_by_id(releve_id)
        if releve.statut != StatutReleve.RELEVE:
            raise ValidationError("Seul un index déjà relevé peut être corrigé (utilisez la saisie d'index).")
        if nouveau_index < releve.ancien_index:
            raise ValidationError(
                f"Le nouvel index ne peut pas être inférieur à l'ancien index ({releve.ancien_index})."
            )
        # Valeur relevée AVANT la correction : c'est elle que l'action remplace
        # (l'« index avant l'action » au sens du proto), pas l'index compteur de
        # base. À capturer avant corriger() qui écrase releve.nouveau_index —
        # sinon la valeur remplacée est perdue et l'audit devient trompeur.
        index_avant_correction = releve.nouveau_index
        with transaction.atomic():
            releve = self._repo.corriger(
                releve=releve,
                nouveau_index=nouveau_index,
                observation=observation,
            )
            self._audit_repo.create(
                releve=releve,
                action=ActionAudit.CORRECTION,
                auteur_id=auteur_id,
                auteur_username=auteur_username,
                auteur_role=auteur_role,
                ancien_index=index_avant_correction,
                nouvel_index=nouveau_index,
            )
        return releve

    def marquer_non_releve(
        self,
        releve_id: str,
        statut: str = StatutReleve.NON_RELEVE,
        observation: str = "",
        agent_id: str = "",
    ) -> Releve:
        if statut not in (StatutReleve.NON_RELEVE, StatutReleve.ESTIME):
            raise ValidationError(f"Statut invalide : {statut}. Valeurs attendues : NON_RELEVE, ESTIME.")
        releve = self._repo.get_by_id(releve_id)
        # Cloisonnement en écriture, symétrique de saisir_index : un AGENT ne
        # peut pas marquer NON_RELEVE/ESTIME un relevé hors de ses zones (les
        # ESTIME sont facturés). Auteur sans zone (ADMIN/SUPERVISEUR/agent
        # global) → non restreint.
        if self._hors_perimetre_zone(releve, agent_id):
            raise ValidationError("Ce relevé est hors de votre périmètre de zones affectées.")
        if releve.campagne.statut != StatutCampagne.EN_COURS:
            raise ValidationError("Le relevé ne peut être modifié que sur une campagne EN_COURS.")
        if releve.statut == StatutReleve.RELEVE:
            raise ValidationError("Un relevé déjà saisi ne peut pas être marqué non-relevé.")
        return self._repo.marquer_non_releve(releve, statut=statut, observation=observation)

    def get_releve(self, releve_id: str) -> Releve:
        return self._repo.get_by_id(releve_id)

    def list_releves(self, campagne_id: str) -> list[Releve]:
        self._campagne_repo.get_by_id(campagne_id)
        return self._repo.list_by_campagne(campagne_id)
