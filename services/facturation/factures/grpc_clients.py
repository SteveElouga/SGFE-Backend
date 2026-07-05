"""Clients gRPC vers les services externes consommés par Facturation Service."""

import logging
import sys
from dataclasses import dataclass
from pathlib import Path

import grpc
from django.conf import settings

from .pdf_generator import InfosSociete

logger = logging.getLogger(__name__)


@dataclass
class AbonneIdentite:
    """Identité de l'abonné affichée sur le PDF de facture (source : Abonné Service)."""

    numero_abonne: str = ""
    nom: str = ""
    prenom: str = ""
    telephone_whatsapp: str = ""
    adresse: str = ""
    numero_compteur: str = ""
    quartier: str = ""
    camp: str = ""


class AbonneServiceClient:
    """Client gRPC vers Abonné Service (port 50052) — identité de l'abonné."""

    def __init__(self) -> None:
        address = f"{settings.ABONNE_GRPC_HOST}:{settings.ABONNE_GRPC_PORT}"
        self._channel = grpc.insecure_channel(address)

        proto_path = str(Path(settings.BASE_DIR) / "proto")
        if proto_path not in sys.path:
            sys.path.insert(0, proto_path)

        import abonne_service_pb2 as pb
        import abonne_service_pb2_grpc as pb_grpc

        self._stub = pb_grpc.AbonneServiceStub(self._channel)
        self._pb = pb

    def get_abonne(self, abonne_id: str) -> AbonneIdentite | None:
        """Retourne l'identité de l'abonné, ou None si Abonné Service est inaccessible.

        Dégradation gracieuse : un PDF reste généré même sans ces données (le
        gabarit affiche alors l'identifiant technique en repli), la facture ne
        doit jamais échouer parce que l'affichage nominatif n'est pas disponible.
        """
        try:
            r = self._stub.GetAbonne(self._pb.AbonneIdRequest(abonne_id=abonne_id))
            compteur = getattr(r, "compteur", None)
            numero_compteur = f"{compteur.numero_compteur:04d}" if compteur and compteur.numero_compteur else ""
            return AbonneIdentite(
                numero_abonne=r.numero_abonne,
                nom=r.nom,
                prenom=r.prenom,
                telephone_whatsapp=r.telephone_whatsapp,
                adresse=r.adresse,
                numero_compteur=numero_compteur,
                quartier=compteur.quartier if compteur else "",
                camp=str(compteur.camp) if compteur and compteur.camp else "",
            )
        except Exception as exc:
            logger.warning(
                "Abonné Service inaccessible — PDF généré sans identité nominative",
                extra={"abonne_id": abonne_id, "error": str(exc)},
            )
            return None


class CampagneServiceClient:
    """Client gRPC vers Campagne Service (port 50053) — lecture des relevés."""

    def __init__(self) -> None:
        address = f"{settings.CAMPAGNE_GRPC_HOST}:{settings.CAMPAGNE_GRPC_PORT}"
        self._channel = grpc.insecure_channel(address)

        proto_path = str(Path(settings.BASE_DIR) / "proto")
        if proto_path not in sys.path:
            sys.path.insert(0, proto_path)

        import campagne_service_pb2 as pb
        import campagne_service_pb2_grpc as pb_grpc

        self._stub = pb_grpc.CampagneServiceStub(self._channel)
        self._pb = pb

    def list_releves(self, campagne_id: str) -> list[dict]:
        """Retourne la liste des relevés RELEVE (index saisi) pour une campagne."""
        try:
            response = self._stub.ListReleves(self._pb.CampagneIdRequest(campagne_id=campagne_id))
            return [
                {
                    "abonne_id": r.abonne_id,
                    "ancien_index": r.ancien_index,
                    "nouveau_index": r.nouveau_index,
                    "consommation": r.consommation,
                    "date_releve": r.date_releve,
                    "statut": r.statut,
                }
                for r in response.releves
                if r.statut == "RELEVE"
            ]
        except grpc.RpcError as exc:
            logger.warning(
                "Erreur gRPC lors de la récupération des relevés",
                extra={"campagne_id": campagne_id, "error": str(exc)},
            )
            raise

    def get_campagne_nom(self, campagne_id: str) -> str:
        """Retourne le nom de la campagne, ou "" si Campagne Service est inaccessible.

        Dégradation gracieuse : purement informatif pour l'affichage sur le PDF
        (période de relevé), la génération de facture ne doit jamais échouer
        pour cette seule donnée.
        """
        try:
            r = self._stub.GetCampagne(self._pb.CampagneIdRequest(campagne_id=campagne_id))
            return r.nom
        except grpc.RpcError as exc:
            logger.warning(
                "Impossible de récupérer le nom de la campagne — PDF généré sans",
                extra={"campagne_id": campagne_id, "error": str(exc)},
            )
            return ""


class ConfigServiceClient:
    """Client gRPC vers Config Service (port 50058) — paramètres système."""

    def __init__(self) -> None:
        address = f"{settings.CONFIG_GRPC_HOST}:{settings.CONFIG_GRPC_PORT}"
        self._channel = grpc.insecure_channel(address)

        proto_path = str(Path(settings.BASE_DIR) / "proto")
        if proto_path not in sys.path:
            sys.path.insert(0, proto_path)

        import config_service_pb2 as pb
        import config_service_pb2_grpc as pb_grpc

        self._stub = pb_grpc.ConfigServiceStub(self._channel)
        self._pb = pb

    def get_delai_paiement_jours(self) -> int:
        """Retourne le délai de paiement (en jours) depuis Config Service.

        Retourne la valeur par défaut (5) si le service est inaccessible.
        """
        try:
            response = self._stub.GetConfig(self._pb.ConfigKeyRequest(cle="delai_paiement_jours"))
            return int(response.valeur)
        except Exception:
            logger.warning(
                "Config Service inaccessible — délai paiement par défaut appliqué",
                extra={"default": settings.DEFAULT_DELAI_PAIEMENT_JOURS},
            )
            return settings.DEFAULT_DELAI_PAIEMENT_JOURS

    def get_infos_societe(self) -> InfosSociete:
        """Retourne les informations de la société pour le PDF.

        Retourne des valeurs vides si le service est inaccessible.
        """
        try:
            r = self._stub.GetInfosSociete(self._pb.EmptyRequest())
            return InfosSociete(nom=r.nom or "SGFE", adresse=r.adresse, telephone=r.telephone)
        except Exception:
            logger.warning("Config Service inaccessible — infos société par défaut pour PDF")
            return InfosSociete()


class NotificationServiceClient:
    """Client gRPC vers Notification Service (port 50056) — envoi WhatsApp facture."""

    def __init__(self) -> None:
        address = f"{settings.NOTIFICATION_GRPC_HOST}:{settings.NOTIFICATION_GRPC_PORT}"
        self._channel = grpc.insecure_channel(address)

        proto_path = str(Path(settings.BASE_DIR) / "proto")
        if proto_path not in sys.path:
            sys.path.insert(0, proto_path)

        import notification_service_pb2 as pb
        import notification_service_pb2_grpc as pb_grpc

        self._stub = pb_grpc.NotificationServiceStub(self._channel)
        self._pb = pb

    def envoyer_facture(self, facture_id: str, abonne_id: str) -> bool:
        """Déclenche l'envoi WhatsApp de la facture via Notification Service.

        Retourne True si OK, False en cas d'erreur (dégradation gracieuse).
        """
        try:
            self._stub.EnvoyerFacture(self._pb.EnvoyerFactureRequest(facture_id=facture_id, abonne_id=abonne_id))
            return True
        except Exception as exc:
            logger.warning(
                "Notification Service inaccessible — EnvoyerFacture ignoré",
                extra={"facture_id": facture_id, "error": str(exc)},
            )
            return False

    def get_espace_url(self, abonne_id: str, facture_id: str) -> tuple[str, str]:
        """Récupère (ou crée) l'URL de l'espace abonné, pour l'afficher sur le PDF.

        Retourne (url, date_expiration ISO), ou ("", "") en cas d'erreur —
        dégradation gracieuse : le bloc « espace abonné » du PDF est alors
        simplement masqué (le lien reste par ailleurs envoyé par WhatsApp).
        """
        try:
            resp = self._stub.GetEspaceUrl(self._pb.GetEspaceUrlRequest(abonne_id=abonne_id, facture_id=facture_id))
            return resp.url, resp.date_expiration
        except Exception as exc:
            logger.warning(
                "Notification Service inaccessible — GetEspaceUrl ignoré",
                extra={"abonne_id": abonne_id, "error": str(exc)},
            )
            return "", ""


class PaiementServiceClient:
    """Client gRPC vers Paiement Service (port 50055) — initialisation du solde."""

    def __init__(self) -> None:
        address = f"{settings.PAIEMENT_GRPC_HOST}:{settings.PAIEMENT_GRPC_PORT}"
        self._channel = grpc.insecure_channel(address)

        proto_path = str(Path(settings.BASE_DIR) / "proto")
        if proto_path not in sys.path:
            sys.path.insert(0, proto_path)

        import paiement_service_pb2 as pb
        import paiement_service_pb2_grpc as pb_grpc

        self._stub = pb_grpc.PaiementServiceStub(self._channel)
        self._pb = pb

    def initialiser_solde(
        self,
        facture_id: str,
        abonne_id: str,
        montant_total: float,
        date_limite_paiement: str,
    ) -> bool:
        """Initialise le solde de la facture dans Paiement Service.

        Retourne True si OK, False en cas d'erreur (dégradation gracieuse).
        """
        try:
            self._stub.InitialiserSolde(
                self._pb.InitialiserSoldeRequest(
                    facture_id=facture_id,
                    abonne_id=abonne_id,
                    montant_total=montant_total,
                    date_limite_paiement=date_limite_paiement,
                )
            )
            return True
        except Exception as exc:
            logger.warning(
                "Paiement Service inaccessible — InitialiserSolde ignoré",
                extra={"facture_id": facture_id, "error": str(exc)},
            )
            return False

    def list_impayes(self) -> list[dict]:
        """Retourne les soldes impayés (facture_id + montants) depuis Paiement Service.

        Dégradation gracieuse : liste vide si le service est inaccessible.
        """
        try:
            response = self._stub.ListImpayes(self._pb.EmptyRequest())
            return [
                {
                    "facture_id": s.facture_id,
                    "montant_total": s.montant_total,
                    "montant_paye": s.montant_paye,
                    "solde_restant": s.solde_restant,
                    "statut": s.statut,
                }
                for s in response.impayes
            ]
        except Exception as exc:
            logger.warning("Paiement Service inaccessible — ListImpayes vide", extra={"error": str(exc)})
            return []

    def get_suivi_impaye(self, facture_id: str) -> dict | None:
        """Retourne le suivi de relance d'une facture (étape, date de dépassement).

        Dégradation gracieuse : None si le service est inaccessible ou sans suivi.
        """
        try:
            s = self._stub.GetSuiviImpaye(self._pb.FactureIdRequest(facture_id=facture_id))
            return {
                "etape_actuelle": s.etape_actuelle,
                "date_depassement": s.date_depassement,
                "resolu_le": s.resolu_le,
            }
        except Exception as exc:
            logger.warning(
                "Paiement Service inaccessible — GetSuiviImpaye ignoré",
                extra={"facture_id": facture_id, "error": str(exc)},
            )
            return None


class ReportingServiceClient:
    """Client gRPC vers Reporting Service (port 50057) — pousse les stats de facturation.

    Le Reporting Service est un read model aval (ADR-019) : son indisponibilité
    ne doit jamais interrompre la génération/mise à jour d'une facture. Toutes
    les méthodes dégradent gracieusement (log + retour False).
    """

    def __init__(self) -> None:
        address = f"{settings.REPORTING_GRPC_HOST}:{settings.REPORTING_GRPC_PORT}"
        self._channel = grpc.insecure_channel(address)

        proto_path = str(Path(settings.BASE_DIR) / "proto")
        if proto_path not in sys.path:
            sys.path.insert(0, proto_path)

        import reporting_service_pb2 as pb
        import reporting_service_pb2_grpc as pb_grpc

        self._stub = pb_grpc.ReportingServiceStub(self._channel)
        self._pb = pb

    def update_stats_facturation(
        self,
        campagne_id: str,
        delta_factures: int,
        delta_montant: float,
        type_update: str,
    ) -> bool:
        """type_update ∈ {GENEREE, ENVOYEE, PAYEE}. Retourne False si Reporting KO."""
        try:
            self._stub.UpdateStatsFacturation(
                self._pb.UpdateStatsFacturationRequest(
                    campagne_id=campagne_id,
                    delta_factures=delta_factures,
                    delta_montant=delta_montant,
                    type_update=type_update,
                )
            )
            return True
        except Exception as exc:
            logger.warning(
                "Reporting Service inaccessible — UpdateStatsFacturation ignoré",
                extra={"campagne_id": campagne_id, "type_update": type_update, "error": str(exc)},
            )
            return False
