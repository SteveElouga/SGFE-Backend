"""Logique métier du Campagne Service."""

from typing import Optional

from django.core.exceptions import ValidationError

from .models import Campagne, Releve, StatutCampagne, StatutReleve
from .repositories import CampagneRepository, ReleveRepository


class CampagneService:
    """Gestion des campagnes de relevé."""

    def __init__(self) -> None:
        self._repo = CampagneRepository()
        self._releve_repo = ReleveRepository()

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

    def ajouter_abonne_campagne(
        self,
        campagne_id: str,
        abonne_id: str,
        ancien_index: float,
    ) -> Releve:
        campagne = self._repo.get_by_id(campagne_id)
        if campagne.statut not in (StatutCampagne.PLANIFIEE, StatutCampagne.EN_COURS):
            raise ValidationError("Impossible d'ajouter un abonné à une campagne clôturée.")
        existant = self._releve_repo.get_by_campagne_abonne(campagne_id, abonne_id)
        if existant:
            raise ValidationError(f"L'abonné {abonne_id} est déjà inscrit à la campagne {campagne_id}.")
        return self._releve_repo.create(campagne=campagne, abonne_id=abonne_id, ancien_index=ancien_index)

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


class ReleveService:
    """Gestion des relevés d'index."""

    def __init__(self) -> None:
        self._repo = ReleveRepository()
        self._campagne_repo = CampagneRepository()

    def saisir_index(
        self,
        releve_id: str,
        nouveau_index: float,
        agent_id: str,
        observation: str = "",
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
        return self._repo.saisir_index(
            releve=releve,
            nouveau_index=nouveau_index,
            agent_id=agent_id,
            observation=observation,
        )

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
