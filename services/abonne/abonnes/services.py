from django.db import transaction
from django.utils.translation import gettext_lazy as _

from abonnes.audit import enregistrer_audit
from abonnes.dtos import ZoneStatDict
from abonnes.models import Abonne, Compteur, HistoriqueCompteur, StatutAbonne, StatutCompteur
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

    # RGPD — droit à l'effacement (anonymiser_abonne). Valeurs EXPLICITES,
    # jamais vidées : quiconque consulte le dossier plus tard doit comprendre
    # que c'est un effacement RGPD délibéré, pas une donnée manquante par
    # erreur. Le téléphone reste au format E.164 attendu par
    # `validate_telephone_whatsapp` (que cette méthode ne rappelle pas, mais
    # dont la forme reste cohérente si un jour un contrôle la relit).
    NOM_ANONYMISE = "Abonné anonymisé"
    PRENOM_ANONYMISE = "(RGPD)"
    TELEPHONE_ANONYMISE = "+00000000000"
    ADRESSE_ANONYMISEE = "Adresse supprimée (RGPD)"

    def __init__(self) -> None:
        self.abonnes = AbonneRepository()
        self.compteurs = CompteurRepository()
        self.numerotation = NumerotationService()

    def get_abonne(self, abonne_id: str) -> Abonne:
        return self.abonnes.get_by_id(abonne_id)

    def list_abonnes(
        self, statut: str | None = None, limit: int | None = None, offset: int | None = None
    ) -> list[Abonne]:
        return self.abonnes.list_all(statut, limit=limit, offset=offset)

    def count_abonnes(self, statut: str | None = None) -> int:
        return self.abonnes.count_all(statut)

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
        position: str = "",
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
                position=position,
            )
            # Pas de PII (nom/prénom/téléphone/adresse — chiffrées au repos,
            # voir abonnes/fields.py) dans le détail d'audit : uniquement des
            # identifiants métier, comme pour le Paiement Service.
            enregistrer_audit(
                action="ABONNE_CREE",
                objet_type="Abonne",
                objet_id=str(abonne.id),
                detail=(
                    f"numero_abonne={numero_abonne} — compteur={numero_compteur} (quartier={quartier}, camp={camp})"
                ),
            )
        return abonne

    def update_abonne(self, abonne_id: str, nom: str, prenom: str, telephone_whatsapp: str, adresse: str) -> Abonne:
        abonne = self.abonnes.get_by_id(abonne_id)
        champs_modifies: list[str] = []
        if nom:
            abonne.nom = nom
            champs_modifies.append("nom")
        if prenom:
            abonne.prenom = prenom
            champs_modifies.append("prenom")
        if telephone_whatsapp:
            abonne.telephone_whatsapp = validate_telephone_whatsapp(telephone_whatsapp)
            champs_modifies.append("telephone_whatsapp")
        if adresse:
            abonne.adresse = adresse
            champs_modifies.append("adresse")
        with transaction.atomic():
            abonne = self.abonnes.save(abonne)
            # Détail = noms des champs touchés, jamais leur valeur (PII).
            enregistrer_audit(
                action="ABONNE_MODIFIE",
                objet_type="Abonne",
                objet_id=str(abonne.id),
                detail=f"champs modifiés : {', '.join(champs_modifies) if champs_modifies else 'aucun'}",
            )
        return abonne

    def suspendre_abonne(self, abonne_id: str) -> Abonne:
        abonne = self.abonnes.get_by_id(abonne_id)
        if abonne.statut != StatutAbonne.ACTIF:
            raise ValidationError(_("Un abonné {statut} ne peut pas être suspendu").format(statut=abonne.statut))
        abonne.statut = StatutAbonne.SUSPENDU
        with transaction.atomic():
            abonne = self.abonnes.save(abonne)
            enregistrer_audit(
                action="ABONNE_SUSPENDU",
                objet_type="Abonne",
                objet_id=str(abonne.id),
                detail=f"numero_abonne={abonne.numero_abonne}",
            )
        return abonne

    def reactiver_abonne(self, abonne_id: str) -> Abonne:
        abonne = self.abonnes.get_by_id(abonne_id)
        if abonne.statut != StatutAbonne.SUSPENDU:
            raise ValidationError(_("Un abonné {statut} ne peut pas être réactivé").format(statut=abonne.statut))
        abonne.statut = StatutAbonne.ACTIF
        with transaction.atomic():
            abonne = self.abonnes.save(abonne)
            enregistrer_audit(
                action="ABONNE_REACTIVE",
                objet_type="Abonne",
                objet_id=str(abonne.id),
                detail=f"numero_abonne={abonne.numero_abonne}",
            )
        return abonne

    def resilier_abonne(self, abonne_id: str) -> Abonne:
        abonne = self.abonnes.get_by_id(abonne_id)
        if abonne.statut == StatutAbonne.RESILIE:
            raise ValidationError(_("Cet abonné est déjà résilié"))
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
            enregistrer_audit(
                action="ABONNE_RESILIE",
                objet_type="Abonne",
                objet_id=str(abonne.id),
                detail=f"numero_abonne={abonne.numero_abonne}",
            )
        return abonne

    def anonymiser_abonne(self, abonne_id: str) -> Abonne:
        """RGPD — droit à l'effacement, à la résiliation.

        N'anonymise QUE l'identité nominative (nom, prénom, téléphone
        WhatsApp, adresse) : `abonne_id`, `numero_abonne` et le statut
        RESILIE sont préservés, ainsi que tout ce que ce service ne possède
        pas (compteur, historique — laissés intacts ; factures/paiements
        vivent dans d'autres services, hors périmètre de cette méthode et
        jamais touchés, pour préserver les obligations comptables de
        conservation).

        Refuse sur un abonné qui n'est pas déjà RESILIE : anonymiser un
        abonné encore ACTIF ou SUSPENDU effacerait l'identité d'une personne
        qui reste cliente du service, ce qu'aucune demande RGPD ne justifie
        avant la fin de la relation contractuelle.

        Idempotent : ré-appeler sur un abonné déjà anonymisé réapplique les
        mêmes valeurs (le statut reste RESILIE) sans erreur.
        """
        abonne = self.abonnes.get_by_id(abonne_id)
        if abonne.statut != StatutAbonne.RESILIE:
            raise ValidationError(
                _("Seul un abonné RESILIE peut être anonymisé (RGPD) — statut actuel : {statut}").format(
                    statut=abonne.statut
                )
            )
        abonne.nom = self.NOM_ANONYMISE
        abonne.prenom = self.PRENOM_ANONYMISE
        abonne.telephone_whatsapp = self.TELEPHONE_ANONYMISE
        abonne.adresse = self.ADRESSE_ANONYMISEE
        return self.abonnes.save(abonne)


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
        position: str | None = None,
    ) -> Compteur:
        compteur = self.compteurs.get_actif(abonne_id)
        champs_modifies: list[str] = []
        if quartier is not None:
            compteur.quartier = quartier
            champs_modifies.append("quartier")
        if camp is not None:
            compteur.camp = camp
            champs_modifies.append("camp")
        if index_initial is not None:
            compteur.index_initial = index_initial
            champs_modifies.append("index_initial")
        if date_pose is not None:
            compteur.date_pose = date_pose
            champs_modifies.append("date_pose")
        if position is not None:
            compteur.position = position
            champs_modifies.append("position")
        with transaction.atomic():
            compteur = self.compteurs.save(compteur)
            enregistrer_audit(
                action="COMPTEUR_MODIFIE",
                objet_type="Compteur",
                objet_id=str(compteur.id),
                detail=(
                    f"abonné={abonne_id} — champs modifiés : "
                    f"{', '.join(champs_modifies) if champs_modifies else 'aucun'}"
                ),
            )
        return compteur

    def get_historique(self, abonne_id: str) -> list[HistoriqueCompteur]:
        return self.historique.list_by_abonne(abonne_id)

    def list_zones(self) -> list[ZoneStatDict]:
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
        nouvelle_position: str = "",
    ) -> Compteur:
        abonne = self.abonnes.get_by_id(abonne_id)
        ancien_compteur = self.compteurs.get_actif(abonne_id)

        if index_fermeture < float(ancien_compteur.index_initial):
            raise ValidationError(_("L'index de fermeture ne peut pas être inférieur à l'index initial"))

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
                position=nouvelle_position,
            )

            self.historique.create(
                abonne=abonne,
                ancien_compteur=ancien_compteur,
                nouveau_compteur=nouveau_compteur,
                index_fermeture=index_fermeture,
                date_remplacement=date_remplacement,
                motif=motif,
            )

            enregistrer_audit(
                action="COMPTEUR_REMPLACE",
                objet_type="Compteur",
                objet_id=str(nouveau_compteur.id),
                detail=(
                    f"abonné={abonne_id} — ancien compteur={ancien_compteur.numero_compteur} "
                    f"remplacé par {nouveau_numero_compteur} — index_fermeture={index_fermeture} — "
                    f"motif={motif!r}"
                ),
            )

        return nouveau_compteur
