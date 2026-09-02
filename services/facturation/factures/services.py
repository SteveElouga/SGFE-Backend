"""Logique métier du Facturation Service."""

import datetime
import logging
import os
from dataclasses import dataclass
from decimal import Decimal, ROUND_HALF_UP
from typing import TYPE_CHECKING

from django.conf import settings
from django.core.exceptions import ObjectDoesNotExist, ValidationError
from django.db import IntegrityError, transaction
from django.utils import timezone

from .event_publisher import publish_reporting_event
from .exceptions import PreconditionError
from .models import Facture, NatureFacture, StatutFacture, Tarif
from .pdf_generator import (
    PDF_TEMPLATE_VERSION,
    DonneesFacture,
    InfosSociete,
    build_historique,
    generer_pdf,
    lire_pdf,
)
from .repositories import FactureRepository, TarifRepository

if TYPE_CHECKING:  # imports réservés au typage — non exécutés (évite la circularité)
    from .grpc_clients import (
        AbonneServiceClient,
        CampagneServiceClient,
        NotificationServiceClient,
        PaiementServiceClient,
    )

logger = logging.getLogger(__name__)

# Tentatives d'allocation d'un numéro de facture séquentiel en cas de collision
# concurrente : le verrou FOR UPDATE ne protège pas la toute première facture
# d'un mois (aucune ligne à verrouiller), on réessaie alors avec un numéro
# recalculé (l'autre transaction a committé le sien entre-temps) — cf. ANO-007.
_MAX_NUMERO_RETRIES = 5


def _date_ou_none(valeur: str, nom: str) -> datetime.date | None:
    """Parse une borne ISO, ou `None` si elle est vide.

    Une date illisible lève plutôt que d'être ignorée : un export silencieusement
    non borné rendrait tout l'historique là où le comptable a demandé un mois, et
    il n'aurait aucun moyen de s'en apercevoir avant de sommer la colonne.
    """
    if not valeur:
        return None
    try:
        return datetime.date.fromisoformat(valeur)
    except ValueError as exc:
        raise ValidationError(f"{nom} doit être une date ISO AAAA-MM-JJ (reçu : {valeur!r}).") from exc


@dataclass
class ReleveData:
    """DTO représentant un relevé provenant du Campagne Service."""

    abonne_id: str
    ancien_index: float
    nouveau_index: float
    consommation: float
    date_releve: str  # ISO date "YYYY-MM-DD"


class TarifService:
    """Gestion du tarif actif (prix du m³)."""

    def __init__(self) -> None:
        self._repo = TarifRepository()

    def get_tarif_actuel(self) -> Tarif:
        """Retourne le tarif actif. Lève ObjectDoesNotExist si aucun tarif n'existe."""
        return self._repo.get_actif()

    def update_tarif(self, prix_m3: Decimal, date_effet: datetime.date) -> Tarif:
        """Désactive l'ancien tarif et crée un nouveau tarif actif.

        La modification n'affecte jamais les factures déjà générées —
        chaque facture conserve le prix_m3 copié au moment de sa création.
        """
        if prix_m3 <= Decimal("0"):
            raise ValidationError("Le prix du m³ doit être strictement positif.")
        with transaction.atomic():
            self._repo.deactivate_all()
            return self._repo.create(prix_m3=prix_m3, date_effet=date_effet)


class FactureService:
    """Génération et gestion des factures."""

    def __init__(
        self,
        paiement_client: "PaiementServiceClient | None" = None,
        notification_client: "NotificationServiceClient | None" = None,
        abonne_client: "AbonneServiceClient | None" = None,
        campagne_client: "CampagneServiceClient | None" = None,
    ) -> None:
        self._repo = FactureRepository()
        self._tarif_repo = TarifRepository()
        # Clients gRPC injectables (défaut = client réel) — permet des tests
        # isolés sans appel réseau. Import tardif : évite la circularité au
        # niveau module (grpc_clients importe des symboles de ce module).
        # Les stats reporting ne passent plus par un client gRPC ici : elles
        # sont publiées en événement (publish_reporting_event).
        from .grpc_clients import (
            AbonneServiceClient,
            CampagneServiceClient,
            NotificationServiceClient,
            PaiementServiceClient,
        )

        self._paiement_client = paiement_client or PaiementServiceClient()
        self._notification_client = notification_client or NotificationServiceClient()
        self._abonne_client = abonne_client or AbonneServiceClient()
        self._campagne_client = campagne_client or CampagneServiceClient()

    def generer_factures(
        self,
        campagne_id: str,
        releves: list[ReleveData],
        delai_paiement_jours: int,
        societe: InfosSociete,
        numero_mobile_money: str = "",
        envoyer_whatsapp_auto: bool = True,
    ) -> list[Facture]:
        """Génère une facture pour chaque relevé RELEVE (index saisi).

        Les relevés NON_RELEVE et ESTIME sont ignorés — pas de facture sans index.
        Le prix_m3 est lu depuis le tarif actif et copié dans chaque facture.
        """
        try:
            tarif = self._tarif_repo.get_actif()
        except ObjectDoesNotExist as exc:
            raise PreconditionError("Aucun tarif actif : configurez un tarif avant de générer des factures.") from exc

        # Récupéré une seule fois pour toute la campagne (dégradation gracieuse
        # vers "" si Campagne Service est inaccessible — purement informatif).
        campagne_nom = self._campagne_client.get_campagne_nom(campagne_id)

        factures: list[Facture] = []

        for releve in releves:
            # Seuls les relevés avec index saisi génèrent une facture
            if releve.nouveau_index is None:
                continue

            # Revalidation défensive : Campagne Service valide déjà
            # nouveau_index >= ancien_index à la saisie, mais Facturation ne
            # doit pas faire une confiance aveugle aux données reçues pour
            # une règle métier obligatoire (voir ANO-008) — un montant
            # négatif ne doit jamais pouvoir être facturé.
            if releve.nouveau_index < releve.ancien_index:
                logger.warning(
                    "Relevé ignoré : nouveau_index < ancien_index",
                    extra={
                        "abonne_id": releve.abonne_id,
                        "ancien_index": releve.ancien_index,
                        "nouveau_index": releve.nouveau_index,
                    },
                )
                continue

            # Campagne Service horodate le relevé (date_releve est un DateTimeField) :
            # date_releve peut donc arriver en date ("YYYY-MM-DD") — ancien seed —
            # OU en datetime ISO ("YYYY-MM-DDTHH:MM:SS+00:00") via le vrai flux
            # SaisirIndex. On n'en garde que la date pour le calcul (ANO-032).
            try:
                date_releve = datetime.date.fromisoformat(releve.date_releve)
            except ValueError:
                date_releve = datetime.datetime.fromisoformat(releve.date_releve).date()
            date_limite = date_releve + datetime.timedelta(days=delai_paiement_jours)

            # Consommation recalculée depuis les index (règle métier
            # `consommation = nouveau_index - ancien_index`) plutôt que reprise
            # du champ `releve.consommation` reçu : le montant facturé ne dépend
            # ainsi que des index imprimés sur la facture, et une éventuelle
            # incohérence amont ne peut pas facturer un montant décorrélé des
            # index (défense en profondeur, cohérente avec ANO-008).
            consommation = (Decimal(str(releve.nouveau_index)) - Decimal(str(releve.ancien_index))).quantize(
                Decimal("0.001"), rounding=ROUND_HALF_UP
            )
            montant = (consommation * tarif.prix_m3).quantize(Decimal("0.01"), rounding=ROUND_HALF_UP)
            annee = date_releve.year
            mois = date_releve.month

            # build_numero(for_update=True) verrouille la dernière facture du mois
            # jusqu'au commit, mais ce verrou ne protège PAS la toute première
            # facture du mois (aucune ligne à verrouiller). On réessaie donc sur
            # collision du numéro unique (ANO-007) : au tour suivant, le max du
            # mois a avancé et build_numero renvoie le numéro suivant.
            for tentative in range(_MAX_NUMERO_RETRIES):
                try:
                    with transaction.atomic():
                        numero = self._repo.build_numero(annee, mois, for_update=True)
                        facture = self._repo.create(
                            abonne_id=releve.abonne_id,
                            campagne_id=campagne_id,
                            ancien_index=Decimal(str(releve.ancien_index)),
                            nouveau_index=Decimal(str(releve.nouveau_index)),
                            consommation=consommation,
                            prix_m3=tarif.prix_m3,
                            montant=montant,
                            date_releve=date_releve,
                            date_limite_paiement=date_limite,
                            numero_facture=numero,
                            numero_mobile_money=numero_mobile_money,
                        )
                    break
                except IntegrityError:
                    if tentative == _MAX_NUMERO_RETRIES - 1:
                        raise
                    logger.warning(
                        "Collision de numéro de facture — nouvelle tentative",
                        extra={"annee": annee, "mois": mois, "tentative": tentative + 1},
                    )

            # Initialise le solde dans Paiement Service (dégradation gracieuse si KO)
            self._paiement_client.initialiser_solde(
                facture_id=str(facture.id),
                abonne_id=releve.abonne_id,
                montant_total=float(facture.montant),
                date_limite_paiement=date_limite.isoformat(),
                campagne_id=campagne_id,
            )

            # ── Le PDF APRÈS le solde, et pas avant ─────────────────────────
            #
            # C'est `initialiser_solde` qui impute l'avoir de l'abonné sur cette
            # facture. Généré avant lui, le PDF interrogeait un solde qui
            # n'existait pas encore : `_lire_avoir_impute` échouait, dégradait à
            # zéro, et le document persisté annonçait le total plein.
            #
            # Le message WhatsApp part ensuite et relit `GetSolde.avoir_impute`,
            # non nul cette fois. Pour un abonné avec un avoir et sans autre
            # facture impayée, l'envoi portait donc DEUX totaux différents : le
            # message en déduisait l'avoir, la pièce jointe non.
            #
            # Le chemin de régénération (`regenerer_facture`) faisait déjà les
            # deux dans cet ordre, avec le commentaire qui l'explique — « le solde
            # récupère au passage l'avoir né de l'annulation ». Deux chemins pour
            # une même chose, l'un juste, l'autre non, et personne ne les avait
            # comparés.
            #
            # Hors de la boucle de réessai : elle protège la numérotation, pas le
            # rendu. Et si le rendu échoue, `get_pdf_bytes` régénère à la demande.
            self._regenerer_et_persister(facture, societe=societe, campagne_nom=campagne_nom)

            # Envoi WhatsApp si activé sur la campagne (dégradation gracieuse si KO)
            if envoyer_whatsapp_auto:
                self._notification_client.envoyer_facture(
                    facture_id=str(facture.id),
                    abonne_id=releve.abonne_id,
                )
            factures.append(facture)

        logger.info(
            "Factures générées pour la campagne",
            extra={"campagne_id": campagne_id, "count": len(factures)},
        )

        # Pousse les stats de facturation au Reporting Service (read model aval,
        # dégradation gracieuse — voir ADR-019). Une seule mise à jour agrégée
        # pour tout le lot généré.
        if factures:
            total_montant = float(sum((f.montant for f in factures), Decimal("0")))
            publish_reporting_event(
                "FACTURATION_STATS",
                campagne_id=campagne_id,
                delta_factures=len(factures),
                delta_montant=total_montant,
                type_update="GENEREE",
            )

        return factures

    def _generer_et_sauver_pdf(self, facture: Facture, societe: InfosSociete, campagne_nom: str = "") -> str:
        """Génère le PDF et retourne son chemin. En cas d'erreur, log et retourne ''."""
        try:
            # Identité de l'abonné pour l'affichage nominatif (dégradation
            # gracieuse : None si Abonné Service est inaccessible → repli sur
            # l'identifiant technique dans le gabarit).
            identite = self._abonne_client.get_abonne(str(facture.abonne_id))
            # Lien de l'espace abonné (get-or-create token côté Notification) —
            # dégradation gracieuse : ("", "") si Notification KO → bloc masqué.
            espace_url, espace_expiration = self._notification_client.get_espace_url(
                abonne_id=str(facture.abonne_id), facture_id=str(facture.id)
            )
            # Ce que l'abonné doit EN PLUS de cette facture. Dégradation
            # gracieuse : si Paiement est injoignable, la facture s'imprime
            # sans la ligne plutôt que d'échouer — mieux vaut une facture
            # incomplète qu'une facture absente.
            dette = self._lire_solde_anterieur(abonne_id=str(facture.abonne_id), facture_id=str(facture.id))
            donnees = DonneesFacture(
                numero_facture=facture.numero_facture,
                abonne_id=str(facture.abonne_id),
                campagne_id=str(facture.campagne_id),
                ancien_index=facture.ancien_index,
                nouveau_index=facture.nouveau_index,
                consommation=facture.consommation,
                prix_m3=facture.prix_m3,
                montant=facture.montant,
                statut=facture.statut,
                date_releve=facture.date_releve.isoformat(),
                date_limite_paiement=facture.date_limite_paiement.isoformat(),
                date_generation=facture.date_generation.strftime("%d/%m/%Y %H:%M"),
                numero_mobile_money=facture.numero_mobile_money,
                numero_abonne=identite.numero_abonne if identite else "",
                abonne_nom=identite.nom if identite else "",
                abonne_prenom=identite.prenom if identite else "",
                abonne_whatsapp=identite.telephone_whatsapp if identite else "",
                abonne_adresse=identite.adresse if identite else "",
                numero_compteur=identite.numero_compteur if identite else "",
                quartier=identite.quartier if identite else "",
                camp=identite.camp if identite else "",
                campagne_nom=campagne_nom,
                espace_url=espace_url,
                espace_date_expiration=espace_expiration,
                nature=facture.nature,
                motif=facture.motif,
                solde_anterieur=dette[0],
                solde_anterieur_nb_factures=dette[1],
                solde_anterieur_depuis=dette[2],
                avoir_impute=self._lire_avoir_impute(str(facture.id)),
            )
            historique = build_historique(
                [
                    (f.date_releve.isoformat(), f.consommation, f.id == facture.id)
                    for f in self._repo.list_historique_consommation(str(facture.abonne_id))
                ]
            )
            return generer_pdf(donnees, societe, settings.PDF_STORAGE_DIR, historique=historique)
        except Exception:
            logger.exception(
                "Erreur lors de la génération PDF",
                extra={"facture_id": str(facture.id)},
            )
            return ""

    def _lire_avoir_impute(self, facture_id: str) -> Decimal:
        """Part de cette facture réglée par un avoir plutôt que par un versement.

        Dégradation gracieuse comme le solde antérieur : si Paiement Service est
        indisponible, la facture s'imprime sans la ligne plutôt que pas du tout.
        Taire un crédit produit une facture incomplète ; ne pas produire de
        facture du tout en produit une inexistante.
        """
        try:
            solde = self._paiement_client.get_solde(facture_id)
            return Decimal(str((solde or {}).get("avoir_impute", 0) or 0))
        except Exception:  # noqa: BLE001 - dégradation explicite
            logger.warning("Avoir imputé indisponible — la facture s'imprime sans la ligne")
            return Decimal("0")

    def _regenerer_et_persister(
        self,
        facture: Facture,
        societe: InfosSociete | None = None,
        campagne_nom: str | None = None,
    ) -> str:
        """Régénère le PDF puis persiste (`pdf_path` + version) si la génération réussit.

        Retourne le chemin du PDF, ou '' en cas d'échec (le PDF existant, s'il y
        en a un, est alors conservé tel quel plutôt qu'écrasé/perdu). `societe`
        et `campagne_nom` peuvent être fournis pour éviter des appels gRPC
        répétés lors d'un traitement par lot.
        """
        if societe is None:
            from .grpc_clients import ConfigServiceClient

            societe = ConfigServiceClient().get_infos_societe()
        if campagne_nom is None:
            campagne_nom = self._campagne_client.get_campagne_nom(str(facture.campagne_id))

        pdf_path = self._generer_et_sauver_pdf(facture, societe, campagne_nom=campagne_nom)
        if pdf_path:
            self._repo.update_pdf_path(facture, pdf_path, PDF_TEMPLATE_VERSION)
        return pdf_path

    def regenerer_pdf(self, facture: Facture, societe: InfosSociete | None = None) -> bool:
        """Régénère et persiste le PDF d'une facture. Retourne True si succès.

        Utilisé par la commande `regenerer_pdfs` pour rafraîchir en masse les
        PDF figés sur un ancien gabarit.
        """
        return bool(self._regenerer_et_persister(facture, societe=societe))

    def get_facture(self, facture_id: str) -> Facture:
        """Retourne une facture par son ID. Lève ObjectDoesNotExist si introuvable."""
        return self._repo.get_by_id(facture_id)

    def list_factures(
        self,
        campagne_id: str = "",
        abonne_id: str = "",
        statut: str = "",
        date_debut: str = "",
        date_fin: str = "",
    ) -> list[Facture]:
        """Retourne les factures filtrées. Tous les paramètres sont optionnels.

        `date_debut` / `date_fin` : bornes ISO `AAAA-MM-JJ` incluses, sur la date
        de génération. Elles rendent possible un journal par période — mois,
        exercice — là où seul le filtre par campagne existait, ce qui laissait
        les régularisations (`campagne_id` vide) exportables par aucun chemin.
        """
        if statut and statut not in StatutFacture.values:
            raise ValidationError(f"Statut invalide : {statut}. Valeurs attendues : {', '.join(StatutFacture.values)}")
        return self._repo.list_by_filters(
            campagne_id=campagne_id,
            abonne_id=abonne_id,
            statut=statut,
            date_debut=_date_ou_none(date_debut, "date_debut"),
            date_fin=_date_ou_none(date_fin, "date_fin"),
        )

    def _lire_solde_anterieur(self, abonne_id: str, facture_id: str) -> tuple[Decimal, int, str]:
        """Interroge Paiement pour la dette de l'abonné, hors la facture imprimée.

        Retourne `(montant, nb_factures, plus_ancienne_echeance)`. Un échec
        renvoie un solde nul : la facture s'imprime sans la ligne plutôt que de
        ne pas s'imprimer du tout.
        """
        try:
            r = self._paiement_client.get_dette_abonne(abonne_id=abonne_id, hors_facture_id=facture_id)
            return Decimal(str(r.total_du)), int(r.nb_factures), r.plus_ancienne_echeance or ""
        except Exception:
            logger.warning(
                "Solde antérieur indisponible — la facture s'imprime sans la ligne",
                extra={"abonne_id": abonne_id, "facture_id": facture_id},
            )
            return Decimal("0"), 0, ""

    def annuler_facture(self, facture_id: str, motif: str, annule_par: str) -> Facture:
        """Annule une facture sans l'effacer.

        Une facture annulée reste au journal avec son numéro, son motif et le nom
        de qui l'a annulée. La supprimer laisserait un trou dans la numérotation
        comptable — et un trou est précisément ce qui prouve qu'on a effacé
        quelque chose.

        Ce que l'abonné avait déjà versé lui revient sous forme d'avoir, d'où il
        s'imputera de lui-même sur sa prochaine facture. C'est le cas courant et
        non l'exception : une erreur d'index se découvre le plus souvent quand
        quelqu'un vient payer et conteste son montant.

        Le motif est obligatoire, comme il l'est pour une régularisation : ces
        deux gestes modifient une dette sans qu'aucun index ne le justifie, et
        la phrase saisie est la seule trace de la raison.
        """
        if not motif or not motif.strip():
            raise ValidationError("Le motif de l'annulation est obligatoire.")

        facture = self._repo.get_by_id(facture_id)
        if facture.statut == StatutFacture.ANNULEE:
            raise ValidationError("Cette facture est déjà annulée.")

        # Éteindre le solde d'abord : si le service paiement est indisponible,
        # mieux vaut ne pas annuler du tout que d'annuler une facture dont la
        # dette continuerait de courir dans les impayés et les relances.
        self._paiement_client.annuler_solde(facture_id=facture_id, motif=motif.strip())

        facture.statut = StatutFacture.ANNULEE
        facture.motif_annulation = motif.strip()
        facture.date_annulation = timezone.now()
        facture.annulee_par = annule_par or ""
        facture.save(update_fields=["statut", "motif_annulation", "date_annulation", "annulee_par"])
        logger.info(
            "Facture annulée",
            extra={"facture_id": facture_id, "numero": facture.numero_facture, "par": annule_par},
        )
        return facture

    def regenerer_facture(
        self,
        facture_id: str,
        motif: str,
        regenere_par: str,
        delai_paiement_jours: int,
        societe: InfosSociete | None = None,
    ) -> tuple[Facture, Facture]:
        """Annule une facture et en émet une corrigée à partir du relevé actuel.

        Le relevé est relu depuis Campagne Service plutôt que recopié de la
        facture : c'est tout l'intérêt du geste. Corriger un index puis
        régénérer produit la facture juste ; recopier les index de l'ancienne
        reproduirait fidèlement l'erreur qu'on cherche à réparer.

        Les deux factures se citent l'une l'autre. Sans ce lien, le journal
        montre une facture annulée et une autre née le même jour, sans rien qui
        dise que la seconde répare la première.

        Ce que l'abonné avait versé revient à son avoir à l'annulation, puis
        s'impute sur la nouvelle facture à sa création : il retrouve son
        paiement sans que personne ne le ressaisisse.

        Returns:
            (facture annulée, facture émise en remplacement)
        """
        ancienne = self._repo.get_by_id(facture_id)
        if ancienne.nature == NatureFacture.REGULARISATION:
            raise ValidationError(
                "Une régularisation ne se régénère pas : son montant est déclaré, pas calculé. "
                "Annulez-la et saisissez-en une nouvelle."
            )
        if not ancienne.campagne_id:
            raise ValidationError("Cette facture n'est rattachée à aucune campagne : régénération impossible.")

        releve = self._relire_releve(ancienne.campagne_id, ancienne.abonne_id)
        if releve is None:
            raise PreconditionError(
                "Aucun relevé trouvé pour cet abonné dans la campagne. Corrigez le relevé avant de régénérer."
            )

        try:
            tarif = self._tarif_repo.get_actif()
        except ObjectDoesNotExist as exc:
            raise PreconditionError("Aucun tarif actif : configurez un tarif avant de régénérer.") from exc

        ancien_index = Decimal(str(releve["ancien_index"]))
        nouveau_index = Decimal(str(releve["nouveau_index"]))
        if nouveau_index < ancien_index:
            raise ValidationError(
                "Le relevé actuel est incohérent (index de fin inférieur à l'index de départ). "
                "Corrigez-le avant de régénérer."
            )

        consommation = (nouveau_index - ancien_index).quantize(Decimal("0.001"), rounding=ROUND_HALF_UP)
        montant = (consommation * tarif.prix_m3).quantize(Decimal("0.01"), rounding=ROUND_HALF_UP)

        try:
            date_releve = datetime.date.fromisoformat(releve["date_releve"])
        except ValueError:
            date_releve = datetime.datetime.fromisoformat(releve["date_releve"]).date()

        # Le délai vient de l'appelant, comme pour `generer_factures` : c'est la
        # couche gRPC qui le lit dans Config Service, pas ce service-ci.
        date_limite = date_releve + datetime.timedelta(days=delai_paiement_jours)

        # L'annulation d'abord : elle rend l'argent versé à l'avoir de l'abonné,
        # d'où la nouvelle facture le récupérera à sa création.
        annulee = self.annuler_facture(facture_id, motif=motif, annule_par=regenere_par)

        campagne_nom = self._campagne_client.get_campagne_nom(ancienne.campagne_id)
        nouvelle = None
        for tentative in range(_MAX_NUMERO_RETRIES):
            try:
                with transaction.atomic():
                    numero = self._repo.build_numero(date_releve.year, date_releve.month, for_update=True)
                    nouvelle = self._repo.create(
                        abonne_id=ancienne.abonne_id,
                        campagne_id=ancienne.campagne_id,
                        ancien_index=ancien_index,
                        nouveau_index=nouveau_index,
                        consommation=consommation,
                        prix_m3=tarif.prix_m3,
                        montant=montant,
                        date_releve=date_releve,
                        date_limite_paiement=date_limite,
                        numero_facture=numero,
                        numero_mobile_money=ancienne.numero_mobile_money,
                        remplace_id=str(ancienne.id),
                    )
                break
            except IntegrityError:
                if tentative == _MAX_NUMERO_RETRIES - 1:
                    raise
        if nouvelle is None:  # pragma: no cover - la boucle sort par break ou raise
            raise RuntimeError("Numérotation de la facture régénérée impossible")

        annulee.remplacee_par_id = str(nouvelle.id)
        annulee.save(update_fields=["remplacee_par_id"])

        # Le solde récupère au passage l'avoir né de l'annulation.
        self._paiement_client.initialiser_solde(
            facture_id=str(nouvelle.id),
            abonne_id=nouvelle.abonne_id,
            campagne_id=nouvelle.campagne_id,
            montant_total=float(montant),
            date_limite_paiement=date_limite.isoformat(),
        )
        self._regenerer_et_persister(nouvelle, societe=societe, campagne_nom=campagne_nom)

        logger.info(
            "Facture régénérée",
            extra={
                "ancienne": annulee.numero_facture,
                "nouvelle": nouvelle.numero_facture,
                "abonne_id": nouvelle.abonne_id,
            },
        )
        return annulee, nouvelle

    def _relire_releve(self, campagne_id: str, abonne_id: str) -> dict | None:
        """Relit le relevé courant d'un abonné dans sa campagne.

        Relu et non recopié : une régénération sert justement à prendre en
        compte un index corrigé entre-temps.
        """
        try:
            releves = self._campagne_client.list_releves(campagne_id)
        except Exception as exc:  # noqa: BLE001 - dégradation explicite
            raise PreconditionError(f"Campagne Service indisponible, régénération impossible : {exc}") from exc
        for r in releves:
            if r.get("abonne_id") == abonne_id and r.get("nouveau_index") is not None:
                return r
        return None

    def creer_regularisation(
        self,
        abonne_id: str,
        montant: float,
        motif: str,
        date_limite_paiement: datetime.date | None = None,
    ) -> Facture:
        """Constate à la main une dette qui existait avant l'application.

        Certains abonnés devaient déjà de l'argent à la mise en service : ces
        arriérés n'avaient aucun moyen d'entrer dans le système, puisqu'une
        facture ne naissait que d'un relevé, à la clôture d'une campagne.

        Une régularisation est une vraie facture — elle passe donc par tout
        l'aval sans rien réécrire : solde, relances, PDF, espace abonné,
        encaissement. Elle s'en distingue sur trois points : sa série de
        numérotation (``REG`` et non ``FACT``), l'absence de campagne et
        d'index, et un motif obligatoire qui dit ce qu'elle constate.

        Son montant est **déclaré**, pas calculé : aucun index ne le justifie.
        C'est pourquoi le motif ne peut pas être vide — il est la seule trace
        de ce que la dette représente.
        """
        montant_d = Decimal(str(montant)).quantize(Decimal("0.01"), rounding=ROUND_HALF_UP)
        if montant_d <= 0:
            raise ValidationError("Le montant de la régularisation doit être supérieur à zéro.")
        if not motif or not motif.strip():
            raise ValidationError("Le motif de la régularisation est obligatoire.")
        if not abonne_id:
            raise ValidationError("L'identifiant de l'abonné est obligatoire.")

        aujourdhui = datetime.date.today()
        # Sans échéance fournie, la dette est exigible immédiatement : elle
        # date d'avant, elle n'a pas à bénéficier d'un nouveau délai.
        limite = date_limite_paiement or aujourdhui

        for tentative in range(_MAX_NUMERO_RETRIES):
            try:
                with transaction.atomic():
                    numero = self._repo.build_numero(aujourdhui.year, aujourdhui.month, for_update=True, serie="REG")
                    facture = self._repo.create(
                        abonne_id=abonne_id,
                        campagne_id="",
                        ancien_index=Decimal("0"),
                        nouveau_index=Decimal("0"),
                        consommation=Decimal("0"),
                        prix_m3=Decimal("0"),
                        montant=montant_d,
                        date_releve=aujourdhui,
                        date_limite_paiement=limite,
                        numero_facture=numero,
                        nature=NatureFacture.REGULARISATION,
                        motif=motif.strip(),
                    )
                break
            except IntegrityError:
                if tentative == _MAX_NUMERO_RETRIES - 1:
                    raise
        else:  # pragma: no cover - la boucle sort toujours par break ou raise
            raise RuntimeError("Numérotation de régularisation impossible")

        # Le solde doit exister pour que la dette apparaisse dans les impayés
        # et entre dans l'escalade des relances comme n'importe quelle facture.
        self._paiement_client.initialiser_solde(
            facture_id=str(facture.id),
            abonne_id=abonne_id,
            campagne_id="",
            montant_total=float(montant_d),
            date_limite_paiement=limite.isoformat(),
        )
        return facture

    def update_statut(self, facture_id: str, statut: str) -> Facture:
        """Met à jour le statut d'une facture (appelé par Paiement Service)."""
        if statut not in StatutFacture.values:
            raise ValidationError(f"Statut invalide : {statut}. Valeurs attendues : {', '.join(StatutFacture.values)}")
        facture = self._repo.get_by_id(facture_id)
        ancien_statut = facture.statut
        facture = self._repo.update_statut(facture, statut)

        # Une facture qui passe PAYEE (depuis un autre statut) est signalée au
        # Reporting Service (read model aval, événementiel durable — ADR-019).
        if statut == StatutFacture.PAYEE and ancien_statut != StatutFacture.PAYEE:
            publish_reporting_event(
                "FACTURATION_STATS",
                campagne_id=str(facture.campagne_id),
                delta_factures=1,
                delta_montant=0.0,
                type_update="PAYEE",
            )
        return facture

    def get_pdf_bytes(self, facture_id: str) -> tuple[bytes, str]:
        """Retourne le contenu PDF et le nom de fichier d'une facture.

        Régénère à la volée si le PDF est manquant OU s'il a été produit par un
        gabarit obsolète (`pdf_template_version` != version courante) — c'est ce
        qui garantit que l'abonné reçoit toujours le rendu à jour après une
        évolution du gabarit. Si la régénération échoue (ex. WeasyPrint
        indisponible), on ressert le PDF existant, même obsolète, plutôt que de
        ne rien renvoyer.
        """
        facture = self._repo.get_by_id(facture_id)

        # Un PDF dont le contenu dépend de données vivantes ne peut pas être
        # mis en cache. C'est le cas dès qu'un solde antérieur s'y imprime : la
        # dette bouge à chaque encaissement, à chaque nouvelle facture, à chaque
        # arriéré saisi. Servir le fichier figé ferait renvoyer à l'abonné un
        # total à payer périmé — précisément ce qu'un renvoi de facture est
        # censé corriger.
        #
        # La régénération ne concerne donc que les abonnés endettés ; pour un
        # abonné à jour, le cache continue de faire son travail.
        dette, _, _ = self._lire_solde_anterieur(abonne_id=str(facture.abonne_id), facture_id=str(facture.id))
        cache_a_jour = (
            facture.pdf_path
            and facture.pdf_template_version == PDF_TEMPLATE_VERSION
            and os.path.exists(facture.pdf_path)
            and dette == 0
        )
        if cache_a_jour:
            return lire_pdf(facture.pdf_path), f"{facture.numero_facture}.pdf"

        pdf_path = self._regenerer_et_persister(facture)
        if pdf_path and os.path.exists(pdf_path):
            return lire_pdf(pdf_path), f"{facture.numero_facture}.pdf"

        # Régénération impossible : repli sur le PDF existant (même obsolète).
        if facture.pdf_path and os.path.exists(facture.pdf_path):
            logger.warning(
                "Régénération PDF impossible — repli sur le PDF stocké (gabarit obsolète)",
                extra={"facture_id": facture_id, "version_stockee": facture.pdf_template_version},
            )
            return lire_pdf(facture.pdf_path), f"{facture.numero_facture}.pdf"

        raise FileNotFoundError(f"Impossible de générer le PDF pour la facture {facture_id}.")


class BilanImpayesService:
    """Génère le PDF « Bilan des impayés » (agrégat back-office ADMIN/COMPTABLE).

    Agrège les impayés (Paiement Service) enrichis du numéro de facture (base
    locale), de l'identité de l'abonné (Abonné Service) et de l'étape de relance
    (Paiement Service), puis rend un document A4 via WeasyPrint. Dégradation
    gracieuse : un service amont indisponible n'empêche pas la génération (les
    champs manquants sont laissés vides / à zéro).
    """

    def __init__(
        self,
        paiement_client: "PaiementServiceClient | None" = None,
        abonne_client: "AbonneServiceClient | None" = None,
        config_client=None,
    ) -> None:
        self._repo = FactureRepository()
        from .grpc_clients import AbonneServiceClient, ConfigServiceClient, PaiementServiceClient

        self._paiement_client = paiement_client or PaiementServiceClient()
        self._abonne_client = abonne_client or AbonneServiceClient()
        self._config_client = config_client or ConfigServiceClient()

    def _build_ligne(self, solde: dict, date_arrete: datetime.date):
        from .bilan_generator import LigneImpaye

        facture_id = solde["facture_id"]
        # Numéro de facture + abonne_id depuis la base locale (facturation possède
        # les factures) ; dégradation si la facture a disparu.
        try:
            facture = self._repo.get_by_id(facture_id)
            numero_facture = facture.numero_facture
            abonne_id = facture.abonne_id
        except ObjectDoesNotExist:
            numero_facture = ""
            abonne_id = ""

        identite = self._abonne_client.get_abonne(abonne_id) if abonne_id else None
        if identite is not None:
            nom_complet = f"{identite.prenom} {identite.nom}".strip()
            numero_abonne = identite.numero_abonne
        else:
            nom_complet = ""
            numero_abonne = ""

        suivi = self._paiement_client.get_suivi_impaye(facture_id)
        etape = 1
        jours_retard = 0
        if suivi:
            etape = suivi.get("etape_actuelle") or 1
            date_dep = suivi.get("date_depassement") or ""
            try:
                jours_retard = (date_arrete - datetime.date.fromisoformat(date_dep[:10])).days
            except (ValueError, TypeError):
                jours_retard = 0

        return LigneImpaye(
            nom_complet=nom_complet,
            numero_abonne=numero_abonne,
            numero_facture=numero_facture,
            montant=solde["montant_total"],
            paye=solde["montant_paye"],
            solde=solde["solde_restant"],
            jours_retard=jours_retard,
            etape=etape,
            en_pause=solde["montant_paye"] > 0,  # un acompte reçu met les relances en pause
        )

    def generer_bilan_impayes_pdf(self) -> tuple[bytes, str]:
        """Retourne (pdf_bytes, filename) du bilan des impayés arrêté ce jour."""
        from .bilan_generator import build_bilan_context, generer_bilan_pdf_bytes

        date_arrete = datetime.date.today()
        impayes = self._paiement_client.list_impayes()
        lignes = [self._build_ligne(s, date_arrete) for s in impayes]
        # Tri par ancienneté décroissante (les plus en retard d'abord).
        lignes.sort(key=lambda ligne: ligne.jours_retard, reverse=True)

        societe = self._config_client.get_infos_societe()
        context = build_bilan_context(lignes, societe, date_arrete)
        pdf_bytes = generer_bilan_pdf_bytes(context)
        filename = f"bilan-impayes-{date_arrete.isoformat()}.pdf"
        return pdf_bytes, filename


class SyntheseCampagneService:
    """Génère le PDF « Synthèse de campagne » (écran 13, back-office ADMIN/COMPTABLE).

    Reprend les statistiques agrégées des trois domaines (relevés, facturation,
    paiements) fournies par le Reporting Service et les met en page via
    WeasyPrint. Lève ObjectDoesNotExist si la campagne n'a aucune statistique
    (Reporting injoignable ou campagne inconnue) — converti en NOT_FOUND côté
    gRPC.
    """

    def __init__(self, reporting_client=None, config_client=None) -> None:
        from .grpc_clients import ConfigServiceClient, ReportingServiceClient

        self._reporting_client = reporting_client or ReportingServiceClient()
        self._config_client = config_client or ConfigServiceClient()

    def generer_synthese_campagne_pdf(self, campagne_id: str) -> tuple[bytes, str]:
        """Retourne (pdf_bytes, filename) de la synthèse de la campagne."""
        from .synthese_generator import build_synthese_context, generer_synthese_pdf_bytes

        stats = self._reporting_client.get_stats_completes(campagne_id)
        if not stats or stats.get("campagne") is None:
            raise ObjectDoesNotExist(f"Aucune statistique pour la campagne : {campagne_id}")

        societe = self._config_client.get_infos_societe()
        date_edition = datetime.date.today()
        context = build_synthese_context(stats, societe, campagne_id, date_edition)
        pdf_bytes = generer_synthese_pdf_bytes(context)
        filename = f"synthese-{campagne_id}-{date_edition.isoformat()}.pdf"
        return pdf_bytes, filename


class RecuPaiementService:
    """Génère le PDF « Reçu de paiement » d'un versement (ADMIN/COMPTABLE).

    Assemble le versement + la situation du solde (Paiement Service, RPC
    ListPaiements/GetSolde existants), l'identité de l'abonné (Abonné Service),
    la facture (base locale) et les infos société (Config Service), puis met en
    page via WeasyPrint. Lève ObjectDoesNotExist si le paiement est introuvable
    (converti en NOT_FOUND côté gRPC) — un reçu ne peut exister sans son
    versement. Dégradation gracieuse pour les données annexes (identité,
    société) : les champs manquants sont laissés vides.
    """

    def __init__(self, paiement_client=None, abonne_client=None, config_client=None) -> None:
        self._repo = FactureRepository()
        from .grpc_clients import AbonneServiceClient, ConfigServiceClient, PaiementServiceClient

        self._paiement_client = paiement_client or PaiementServiceClient()
        self._abonne_client = abonne_client or AbonneServiceClient()
        self._config_client = config_client or ConfigServiceClient()

    @staticmethod
    def _numero_recu(facture, versement: dict, paiements: list[dict]) -> str:
        """Numéro de reçu stable : REC-<suffixe facture>-<rang chronologique>.

        Le rang est calculé sur l'ensemble des versements de la facture triés par
        date de création (annulés inclus) afin qu'il ne change jamais pour un
        versement donné, même si un versement antérieur est annulé plus tard.
        """
        numero = facture.numero_facture or ""
        suffixe = numero.split("-", 1)[1] if "-" in numero else numero
        ordonnes = sorted(paiements, key=lambda p: p.get("created_at") or "")
        rang = next(
            (i for i, p in enumerate(ordonnes, start=1) if p["paiement_id"] == versement["paiement_id"]),
            1,
        )
        return f"REC-{suffixe}-{rang}"

    @staticmethod
    def _heure(created_at: str) -> str:
        """Extrait « HHhMM » d'un horodatage ISO ('' si illisible)."""
        hhmm = (created_at or "")[11:16]
        if len(hhmm) == 5 and hhmm[2] == ":":
            return f"{hhmm[:2]}h{hhmm[3:]}"
        return ""

    def generer_recu_pdf(
        self,
        paiement_id: str,
        facture_id: str,
        montant_versement: float = 0.0,
        solde_restant_total: float = 0.0,
    ) -> tuple[bytes, str]:
        """Retourne (pdf_bytes, filename) du reçu du versement `paiement_id`."""
        from .pdf_generator import _periode_fr
        from .recu_generator import DonneesRecu, build_recu_context, generer_recu_pdf_bytes

        paiements = self._paiement_client.list_paiements(facture_id)
        versement = next((p for p in paiements if p.get("paiement_id") == paiement_id), None)
        if versement is None:
            raise ObjectDoesNotExist(f"Paiement introuvable : {paiement_id}")

        # Un reçu ne peut pas attester ce qu'on n'a pas pu lire.
        #
        # `get_solde` dégrade en `None` quand Paiement Service est injoignable.
        # Le code écrivait `or {}`, puis `solde.get("solde_restant") or 0` — ce qui
        # transforme « inconnu » en « zéro ». Or la note du reçu s'écrit à partir
        # de ce zéro :
        #
        #     « Facture soldée — ce reçu confirme le règlement intégral … »
        #
        # Un appel gRPC en échec produisait donc un DOCUMENT OFFICIEL remis à
        # l'abonné, attestant un règlement intégral qui n'avait pas eu lieu, avec
        # « total versé : 0 » imprimé juste au-dessus.
        #
        # On refuse. L'appelant dégrade déjà proprement : `generer_recu_paiement_pdf`
        # rend `(b"", "")` et le message WhatsApp part sans pièce jointe. Pas de
        # reçu vaut infiniment mieux qu'un faux reçu.
        solde = self._paiement_client.get_solde(facture_id)
        if solde is None:
            raise ObjectDoesNotExist(
                f"Solde de la facture {facture_id} illisible (Paiement Service injoignable) : "
                "un reçu ne peut pas être émis sans connaître le solde."
            )
        nb_versements = sum(1 for p in paiements if not p.get("annule"))

        facture = self._repo.get_by_id(facture_id)  # ObjectDoesNotExist si absente
        abonne_id = versement.get("abonne_id") or ""
        identite = self._abonne_client.get_abonne(abonne_id) if abonne_id else None
        societe = self._config_client.get_infos_societe()

        donnees = DonneesRecu(
            numero_recu=self._numero_recu(facture, versement, paiements),
            date_paiement=versement.get("date_paiement") or "",
            montant=Decimal(str(versement.get("montant") or 0)),
            # Le versement tel que le caissier l'a reçu, et la dette qui reste —
            # transmis par Paiement Service via Notification. À zéro (régénération
            # manuelle depuis le back-office), le reçu retombe sur l'imputation
            # seule, comme avant.
            montant_versement=Decimal(str(montant_versement or 0)),
            solde_restant_total=Decimal(str(solde_restant_total or 0)),
            mode_paiement=versement.get("mode_paiement") or "",
            reference_transaction=versement.get("reference_transaction") or "",
            enregistre_par=versement.get("enregistre_par") or "",
            montant_total=Decimal(str(solde.get("montant_total") or facture.montant)),
            total_verse=Decimal(str(solde.get("montant_paye") or 0)),
            nb_versements=nb_versements or 1,
            solde_restant=Decimal(str(solde.get("solde_restant") or 0)),
            statut=solde.get("statut") or facture.statut,
            numero_facture=facture.numero_facture,
            facture_periode=_periode_fr(facture.date_releve.isoformat()),
            abonne_nom=identite.nom if identite else "",
            abonne_prenom=identite.prenom if identite else "",
            numero_abonne=identite.numero_abonne if identite else "",
            quartier=identite.quartier if identite else "",
            camp=identite.camp if identite else "",
            abonne_id=abonne_id,
            heure_paiement=self._heure(versement.get("created_at") or ""),
        )
        context = build_recu_context(donnees, societe)
        pdf_bytes = generer_recu_pdf_bytes(context)
        return pdf_bytes, f"{donnees.numero_recu}.pdf"
