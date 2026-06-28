from abonnes.models import Abonne, Compteur, StatutAbonne, StatutCompteur
from abonnes.repositories import AbonneRepository, CompteurRepository, HistoriqueCompteurRepository


class ValidationError(Exception):
    """Violation d'une règle métier (ex. abonné non actif, index invalide)."""


class NumerotationService:
    """Génère le numéro auto-incrémenté AB-XXXX (EF-ABO-001)."""

    PREFIX = "AB-"
    WIDTH = 4

    def __init__(self) -> None:
        self.abonnes = AbonneRepository()

    def generer(self) -> str:
        last = self.abonnes.last_numero()
        last_n = int(last.removeprefix(self.PREFIX)) if last else 0
        return f"{self.PREFIX}{last_n + 1:0{self.WIDTH}d}"


class AbonneService:
    """CRUD abonnés + suspension/réactivation (EF-ABO-001 à EF-ABO-004)."""

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
        numero_abonne = self.numerotation.generer()
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
            abonne.telephone_whatsapp = telephone_whatsapp
        if adresse:
            abonne.adresse = adresse
        return self.abonnes.save(abonne)

    def suspendre_abonne(self, abonne_id: str) -> Abonne:
        abonne = self.abonnes.get_by_id(abonne_id)
        abonne.statut = StatutAbonne.SUSPENDU
        return self.abonnes.save(abonne)

    def reactiver_abonne(self, abonne_id: str) -> Abonne:
        abonne = self.abonnes.get_by_id(abonne_id)
        abonne.statut = StatutAbonne.ACTIF
        return self.abonnes.save(abonne)


class CompteurService:
    """Gestion du compteur actif et de son remplacement (EF-ABO-005, EF-ABO-006)."""

    def __init__(self) -> None:
        self.abonnes = AbonneRepository()
        self.compteurs = CompteurRepository()
        self.historique = HistoriqueCompteurRepository()

    def get_compteur_actif(self, abonne_id: str) -> Compteur:
        return self.compteurs.get_actif(abonne_id)

    def remplacer_compteur(
        self,
        abonne_id: str,
        index_fermeture: float,
        nouveau_numero_compteur: int,
        nouveau_quartier: str,
        nouveau_camp: int,
        nouvel_index_initial: float,
        date_remplacement: str,
    ) -> Compteur:
        abonne = self.abonnes.get_by_id(abonne_id)
        ancien_compteur = self.compteurs.get_actif(abonne_id)

        if index_fermeture < float(ancien_compteur.index_initial):
            raise ValidationError("L'index de fermeture ne peut pas être inférieur à l'index initial")

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
        )

        return nouveau_compteur
