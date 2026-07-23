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

from .event_publisher import publish_reporting_event
from .exceptions import PreconditionError
from .models import Facture, StatutFacture, Tarif
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
            raise PreconditionError("Aucun tarif actif — configurez un tarif avant de générer des factures.") from exc

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
                        self._regenerer_et_persister(facture, societe=societe, campagne_nom=campagne_nom)
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
    ) -> list[Facture]:
        """Retourne les factures filtrées. Tous les paramètres sont optionnels."""
        if statut and statut not in StatutFacture.values:
            raise ValidationError(f"Statut invalide : {statut}. Valeurs attendues : {', '.join(StatutFacture.values)}")
        return self._repo.list_by_filters(campagne_id=campagne_id, abonne_id=abonne_id, statut=statut)

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

        cache_a_jour = (
            facture.pdf_path
            and facture.pdf_template_version == PDF_TEMPLATE_VERSION
            and os.path.exists(facture.pdf_path)
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

    def generer_recu_pdf(self, paiement_id: str, facture_id: str) -> tuple[bytes, str]:
        """Retourne (pdf_bytes, filename) du reçu du versement `paiement_id`."""
        from .pdf_generator import _periode_fr
        from .recu_generator import DonneesRecu, build_recu_context, generer_recu_pdf_bytes

        paiements = self._paiement_client.list_paiements(facture_id)
        versement = next((p for p in paiements if p.get("paiement_id") == paiement_id), None)
        if versement is None:
            raise ObjectDoesNotExist(f"Paiement introuvable : {paiement_id}")

        solde = self._paiement_client.get_solde(facture_id) or {}
        nb_versements = sum(1 for p in paiements if not p.get("annule"))

        facture = self._repo.get_by_id(facture_id)  # ObjectDoesNotExist si absente
        abonne_id = versement.get("abonne_id") or ""
        identite = self._abonne_client.get_abonne(abonne_id) if abonne_id else None
        societe = self._config_client.get_infos_societe()

        donnees = DonneesRecu(
            numero_recu=self._numero_recu(facture, versement, paiements),
            date_paiement=versement.get("date_paiement") or "",
            montant=Decimal(str(versement.get("montant") or 0)),
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
