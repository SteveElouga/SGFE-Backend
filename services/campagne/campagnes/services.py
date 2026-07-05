"""Logique métier du Campagne Service."""

from typing import Optional

import grpc
from django.core.exceptions import ValidationError
from django.db import transaction

from .grpc_clients import AbonneServiceClient
from .models import ActionAudit, Campagne, Releve, StatutCampagne, StatutReleve
from .repositories import (
    AffectationZoneRepository,
    CampagneAgentRepository,
    CampagneRepository,
    ReleveAuditRepository,
    ReleveRepository,
)


class CampagneService:
    """Gestion des campagnes de relevé."""

    def __init__(self) -> None:
        self._repo = CampagneRepository()
        self._releve_repo = ReleveRepository()
        self._agent_repo = CampagneAgentRepository()
        self._zone_repo = AffectationZoneRepository()
        self._abonne_client = AbonneServiceClient()

    def _verifier_abonne_actif(self, abonne_id: str):
        """Vérifie que l'abonné est ACTIF et le retourne (avec son compteur).

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
                f"L'abonné {abonne_id} n'est pas ACTIF (statut actuel : {abonne.statut}) — "
                "un abonné suspendu ou résilié ne peut pas être relevé."
            )
        return abonne

    @staticmethod
    def _zone_de(abonne) -> tuple[str, Optional[int]]:
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

    def ajouter_abonne_campagne(
        self,
        campagne_id: str,
        abonne_id: str,
        ancien_index: float,
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
                    pass
        return demarrees

    def affecter_zones(
        self,
        campagne_id: str,
        agent_id: str,
        zones: list[tuple[str, int]],
    ) -> list[dict]:
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

    def list_agents_campagne(self, campagne_id: str) -> list[dict]:
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

        zones_by_agent: dict[str, list] = defaultdict(list)
        for z in zones:
            zones_by_agent[z.agent_id].append(z)

        # Union ordonnée : agents affectés globalement puis agents ayant des zones.
        agent_ids = list(dict.fromkeys(global_ids + list(zones_by_agent.keys())))

        agents: list[dict] = []
        for agent_id in agent_ids:
            stats = agent_stats.get(agent_id, {})
            agents.append(
                {
                    "agent_id": agent_id,
                    "zones": [
                        {
                            "quartier": z.quartier,
                            "camp": z.camp,
                            "nb_releves": zone_counts.get((z.quartier, z.camp), 0),
                        }
                        for z in zones_by_agent.get(agent_id, [])
                    ],
                    "nb_releves": stats.get("nb_releves", 0),
                    "derniere_activite": stats.get("derniere_activite"),
                }
            )
        return agents


class ReleveService:
    """Gestion des relevés d'index."""

    def __init__(self) -> None:
        self._repo = ReleveRepository()
        self._campagne_repo = CampagneRepository()
        self._audit_repo = ReleveAuditRepository()

    def saisir_index(
        self,
        releve_id: str,
        nouveau_index: float,
        agent_id: str,
        observation: str = "",
        auteur_username: str = "",
        auteur_role: str = "",
    ) -> Releve:
        releve = self._repo.get_by_id(releve_id)
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
        nouveau_index: float,
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
                ancien_index=releve.ancien_index,
                nouvel_index=nouveau_index,
            )
        return releve

    def marquer_non_releve(
        self,
        releve_id: str,
        statut: str = StatutReleve.NON_RELEVE,
        observation: str = "",
    ) -> Releve:
        if statut not in (StatutReleve.NON_RELEVE, StatutReleve.ESTIME):
            raise ValidationError(f"Statut invalide : {statut}. Valeurs attendues : NON_RELEVE, ESTIME.")
        releve = self._repo.get_by_id(releve_id)
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
