from django.db import transaction

from abonnes.models import Abonne, Compteur, StatutAbonne, StatutCompteur
from abonnes.repositories import AbonneRepository, CompteurRepository, HistoriqueCompteurRepository
from abonnes.validators import ValidationError, validate_telephone_whatsapp

__all__ = ["ValidationError", "NumerotationService", "AbonneService", "CompteurService"]


class NumerotationService:
    """Génère le numéro auto-incrémenté AB-XXXX (EF-ABO-001)."""

    PREFIX = "AB-"
    WIDTH = 4

    def __init__(self) -> None:
        self.abonnes = AbonneRepository()

    def generer(self, for_update: bool = False) -> str:
        last = self.abonnes.last_numero(for_update=for_update)
        last_n = int(last.removeprefix(self.PREFIX)) if last else 0
        return f"{self.PREFIX}{last_n + 1:0{self.WIDTH}d}"


class AbonneService:
    """CRUD abonnés + suspension/réactivation/résiliation (EF-ABO-001 à EF-ABO-004)."""

    def __init__(self) -> None:
        self.abonnes = AbonneRepository()
        self.compteurs = CompteurRepository()
        self.numerotation = NumerotationService()

    def get_abonne(self, abonne_id: str) -> Abonne:
        return self.abonnes.get_by_id(abonne_id)

    def list_abonnes(self, statut: str | None = None) -> list[Abonne]:
        return self.abonnes.list_all(statut)

    def list_abonnes_actifs(self) -> list[Abonne]:
        return self.abonnes.list_actifs()

    def create_abonne(
        self,
        nom: str,
        prenom: str,
        telephone_whatsapp: str,
        adresse: str,
        numero_compteur: int,
        quartier: str,
        camp: int,
        index_initial: float,
        date_pose: str,
    ) -> Abonne:
        # Un compteur est obligatoire à la création (EF-ABO-001).
        telephone_whatsapp = validate_telephone_whatsapp(telephone_whatsapp)
        with transaction.atomic():
            # select_for_update (dans generer) sérialise la génération du
            # numéro entre transactions concurrentes pour éviter une
            # collision AB-XXXX (cf. NumerotationService.generer).
            numero_abonne = self.numerotation.generer(for_update=True)
            abonne = self.abonnes.create(
                numero_abonne=numero_abonne,
                nom=nom,
                prenom=prenom,
                telephone_whatsapp=telephone_whatsapp,
                adresse=adresse,
            )
            self.compteurs.create(
                abonne=abonne,
                numero_compteur=numero_compteur,
                quartier=quartier,
                camp=camp,
                index_initial=index_initial,
                date_pose=date_pose,
            )
        return abonne

    def update_abonne(self, abonne_id: str, nom: str, prenom: str, telephone_whatsapp: str, adresse: str) -> Abonne:
        abonne = self.abonnes.get_by_id(abonne_id)
        if nom:
            abonne.nom = nom
        if prenom:
            abonne.prenom = prenom
        if telephone_whatsapp:
            abonne.telephone_whatsapp = validate_telephone_whatsapp(telephone_whatsapp)
        if adresse:
            abonne.adresse = adresse
        return self.abonnes.save(abonne)

    def suspendre_abonne(self, abonne_id: str) -> Abonne:
        abonne = self.abonnes.get_by_id(abonne_id)
        if abonne.statut != StatutAbonne.ACTIF:
            raise ValidationError(f"Un abonné {abonne.statut} ne peut pas être suspendu")
        abonne.statut = StatutAbonne.SUSPENDU
        return self.abonnes.save(abonne)

    def reactiver_abonne(self, abonne_id: str) -> Abonne:
        abonne = self.abonnes.get_by_id(abonne_id)
        if abonne.statut != StatutAbonne.SUSPENDU:
            raise ValidationError(f"Un abonné {abonne.statut} ne peut pas être réactivé")
        abonne.statut = StatutAbonne.ACTIF
        return self.abonnes.save(abonne)

    def resilier_abonne(self, abonne_id: str) -> Abonne:
        abonne = self.abonnes.get_by_id(abonne_id)
        if abonne.statut == StatutAbonne.RESILIE:
            raise ValidationError("Cet abonné est déjà résilié")
        with transaction.atomic():
            abonne.statut = StatutAbonne.RESILIE
            self.abonnes.save(abonne)
            # Le compteur actif est désactivé avec la résiliation : il n'est
            # ni remplacé (REMPLACE) ni encore en service, juste hors service
            # tant que la ligne d'eau reste résiliée (ANO-017).
            try:
                compteur = self.compteurs.get_actif(abonne_id)
                compteur.statut = StatutCompteur.DESACTIVE
                self.compteurs.save(compteur)
            except Compteur.DoesNotExist:
                pass
        return abonne


class CompteurService:
    """Gestion du compteur actif et de son remplacement (EF-ABO-005, EF-ABO-006)."""

    def __init__(self) -> None:
        self.abonnes = AbonneRepository()
        self.compteurs = CompteurRepository()
        self.historique = HistoriqueCompteurRepository()

    def get_compteur_actif(self, abonne_id: str) -> Compteur:
        return self.compteurs.get_actif(abonne_id)

    def update_compteur(
        self,
        abonne_id: str,
        quartier: str | None,
        camp: int | None,
        index_initial: float | None,
        date_pose: str | None,
    ) -> Compteur:
        compteur = self.compteurs.get_actif(abonne_id)
        if quartier is not None:
            compteur.quartier = quartier
        if camp is not None:
            compteur.camp = camp
        if index_initial is not None:
            compteur.index_initial = index_initial
        if date_pose is not None:
            compteur.date_pose = date_pose
        return self.compteurs.save(compteur)

    def get_historique(self, abonne_id: str) -> list:
        return self.historique.list_by_abonne(abonne_id)

    def list_zones(self) -> list[dict]:
        """Zones de relevé (quartier, camp) et nombre d'abonnés actifs par zone."""
        return self.compteurs.list_zones()

    def remplacer_compteur(
        self,
        abonne_id: str,
        index_fermeture: float,
        nouveau_numero_compteur: int,
        nouveau_quartier: str,
        nouveau_camp: int,
        nouvel_index_initial: float,
        date_remplacement: str,
        motif: str = "",
    ) -> Compteur:
        abonne = self.abonnes.get_by_id(abonne_id)
        ancien_compteur = self.compteurs.get_actif(abonne_id)

        if index_fermeture < float(ancien_compteur.index_initial):
            raise ValidationError("L'index de fermeture ne peut pas être inférieur à l'index initial")

        with transaction.atomic():
            ancien_compteur.statut = StatutCompteur.REMPLACE
            self.compteurs.save(ancien_compteur)

            nouveau_compteur = self.compteurs.create(
                abonne=abonne,
                numero_compteur=nouveau_numero_compteur,
                quartier=nouveau_quartier,
                camp=nouveau_camp,
                index_initial=nouvel_index_initial,
                date_pose=date_remplacement,
            )

            self.historique.create(
                abonne=abonne,
                ancien_compteur=ancien_compteur,
                nouveau_compteur=nouveau_compteur,
                index_fermeture=index_fermeture,
                date_remplacement=date_remplacement,
                motif=motif,
            )

        return nouveau_compteur
