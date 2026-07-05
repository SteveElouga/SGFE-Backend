from typing import Optional

from django.core.exceptions import ObjectDoesNotExist
from django.utils import timezone

from .models import (
    Campagne,
    CampagneAgent,
    Releve,
    ReleveAudit,
    StatutCampagne,
    StatutReleve,
)


class CampagneRepository:
    """Accès base de données pour les campagnes."""

    def create(
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
        statut = StatutCampagne.EN_COURS if demarrer_maintenant else StatutCampagne.PLANIFIEE
        return Campagne.objects.create(
            nom=nom,
            periode_mois=periode_mois,
            periode_annee=periode_annee,
            created_by=created_by,
            date_planifiee=date_planifiee,
            numero_mobile_money=numero_mobile_money,
            generer_factures_auto=generer_factures_auto,
            envoyer_whatsapp_auto=envoyer_whatsapp_auto,
            statut=statut,
        )

    def get_by_id(self, campagne_id: str) -> Campagne:
        try:
            return Campagne.objects.get(pk=campagne_id)
        except Campagne.DoesNotExist:
            raise ObjectDoesNotExist(f"Campagne introuvable : {campagne_id}")

    def list_all(self, created_by: str = "", agent_id: str = "") -> list[Campagne]:
        qs = Campagne.objects.all()
        if created_by:
            qs = qs.filter(created_by=created_by)
        if agent_id:
            qs = qs.filter(agents_affectes__agent_id=agent_id)
        return list(qs)

    def list_en_cours(self) -> list[Campagne]:
        return list(Campagne.objects.filter(statut=StatutCampagne.EN_COURS))

    def update_statut(self, campagne: Campagne, statut: str) -> Campagne:
        campagne.statut = statut
        if statut == StatutCampagne.CLOTUREE:
            campagne.date_cloture = timezone.now()
        campagne.save(update_fields=["statut", "date_cloture"])
        return campagne

    def list_planifiees_pour_date(self, date_planifiee) -> list[Campagne]:
        """Retourne TOUTES les campagnes PLANIFIEE pour cette date (voir ANO-019 —
        `.first()` ne démarrait auparavant qu'une seule campagne par jour cible,
        même si plusieurs partageaient la même date_planifiee)."""
        return list(
            Campagne.objects.filter(
                statut=StatutCampagne.PLANIFIEE,
                date_planifiee=date_planifiee,
            )
        )


class ReleveRepository:
    """Accès base de données pour les relevés."""

    def create(
        self,
        campagne: Campagne,
        abonne_id: str,
        ancien_index: float,
    ) -> Releve:
        return Releve.objects.create(
            campagne=campagne,
            abonne_id=abonne_id,
            ancien_index=ancien_index,
            statut=StatutReleve.A_RELEVER,
        )

    def get_by_id(self, releve_id: str) -> Releve:
        try:
            return Releve.objects.select_related("campagne").get(pk=releve_id)
        except Releve.DoesNotExist:
            raise ObjectDoesNotExist(f"Relevé introuvable : {releve_id}")

    def list_by_campagne(self, campagne_id: str) -> list[Releve]:
        return list(
            Releve.objects.filter(campagne_id=campagne_id).select_related("campagne").prefetch_related("audits")
        )

    def get_by_campagne_abonne(self, campagne_id: str, abonne_id: str) -> Optional[Releve]:
        return Releve.objects.filter(campagne_id=campagne_id, abonne_id=abonne_id).first()

    def saisir_index(
        self,
        releve: Releve,
        nouveau_index: float,
        agent_id: str,
        observation: str = "",
    ) -> Releve:
        consommation = nouveau_index - releve.ancien_index
        releve.nouveau_index = nouveau_index
        releve.consommation = consommation
        releve.agent_id = agent_id
        releve.observation = observation
        releve.statut = StatutReleve.RELEVE
        releve.date_releve = timezone.now()
        releve.save(
            update_fields=[
                "nouveau_index",
                "consommation",
                "agent_id",
                "observation",
                "statut",
                "date_releve",
            ]
        )
        return releve

    def corriger(
        self,
        releve: Releve,
        nouveau_index: float,
        observation: str = "",
    ) -> Releve:
        """Corrige la valeur d'un relevé déjà saisi.

        Ne touche ni à ``agent_id`` (l'auteur d'origine reste tracé) ni à
        ``date_releve`` (la période relevée est inchangée) : seul l'index et
        la consommation sont recalculés. La traçabilité de la correction est
        assurée par une entrée ``ReleveAudit`` distincte.
        """
        releve.nouveau_index = nouveau_index
        releve.consommation = nouveau_index - releve.ancien_index
        if observation:
            releve.observation = observation
        releve.save(update_fields=["nouveau_index", "consommation", "observation"])
        return releve

    def marquer_non_releve(
        self,
        releve: Releve,
        statut: str = StatutReleve.NON_RELEVE,
        observation: str = "",
    ) -> Releve:
        releve.statut = statut
        releve.observation = observation
        releve.save(update_fields=["statut", "observation"])
        return releve

    def count_by_campagne(self, campagne_id: str) -> dict[str, int]:
        """Retourne le nombre de relevés par statut pour une campagne."""
        from django.db.models import Count

        counts = Releve.objects.filter(campagne_id=campagne_id).values("statut").annotate(total=Count("id"))
        result: dict[str, int] = {s: 0 for s in StatutReleve.values}
        for row in counts:
            result[row["statut"]] = row["total"]
        return result


class ReleveAuditRepository:
    """Accès base de données pour le journal d'audit des relevés."""

    def create(
        self,
        releve: Releve,
        action: str,
        auteur_id: str,
        auteur_username: str = "",
        auteur_role: str = "",
        ancien_index: Optional[float] = None,
        nouvel_index: Optional[float] = None,
    ) -> ReleveAudit:
        return ReleveAudit.objects.create(
            releve=releve,
            action=action,
            auteur_id=auteur_id,
            auteur_username=auteur_username,
            auteur_role=auteur_role,
            ancien_index=ancien_index,
            nouvel_index=nouvel_index,
        )

    def list_by_releve(self, releve: Releve) -> list[ReleveAudit]:
        return list(releve.audits.all())


class CampagneAgentRepository:
    """Accès base de données pour les affectations agent-campagne."""

    def assigner(self, campagne: Campagne, agent_id: str) -> CampagneAgent:
        """Affecte un agent à une campagne (idempotent — ignoré si déjà affecté)."""
        obj, _ = CampagneAgent.objects.get_or_create(
            campagne=campagne,
            agent_id=agent_id,
        )
        return obj

    def est_affecte(self, campagne_id: str, agent_id: str) -> bool:
        return CampagneAgent.objects.filter(campagne_id=campagne_id, agent_id=agent_id).exists()
