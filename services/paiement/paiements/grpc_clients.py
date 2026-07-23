"""Clients gRPC vers les services externes consommés par Paiement Service."""

import logging
import sys
from pathlib import Path

import grpc
from django.conf import settings

logger = logging.getLogger(__name__)


def _proto_path() -> str:
    """Retourne le chemin du dossier proto/ du service."""
    return str(Path(settings.BASE_DIR) / "proto")


def _ensure_proto_in_syspath() -> None:
    """Assure que le dossier proto/ est dans sys.path pour les imports générés."""
    path = _proto_path()
    if path not in sys.path:
        sys.path.insert(0, path)


class FacturationServiceClient:
    """Client gRPC vers Facturation Service (port 50054)."""

    def __init__(self) -> None:
        address = f"{settings.FACTURATION_GRPC_HOST}:{settings.FACTURATION_GRPC_PORT}"
        self._channel = grpc.insecure_channel(address)
        _ensure_proto_in_syspath()

        import facturation_service_pb2 as pb
        import facturation_service_pb2_grpc as pb_grpc

        self._stub = pb_grpc.FacturationServiceStub(self._channel)
        self._pb = pb

    def update_statut_facture(self, facture_id: str, statut: str) -> None:
        """
        Met à jour le statut d'une facture dans Facturation Service.
        Dégradation gracieuse en cas d'erreur gRPC.
        """
        try:
            self._stub.UpdateStatutFacture(self._pb.UpdateStatutRequest(facture_id=facture_id, statut=statut))
            logger.info(
                "Statut facture mis à jour via Facturation Service",
                extra={"facture_id": facture_id, "statut": statut},
            )
        except grpc.RpcError as exc:
            logger.warning(
                "UpdateStatutFacture échoué — dégradation gracieuse",
                extra={"facture_id": facture_id, "statut": statut, "error": str(exc)},
            )


class NotificationServiceClient:
    """Client gRPC vers Notification Service (port 50056)."""

    def __init__(self) -> None:
        address = f"{settings.NOTIFICATION_GRPC_HOST}:{settings.NOTIFICATION_GRPC_PORT}"
        self._channel = grpc.insecure_channel(address)
        _ensure_proto_in_syspath()

        import notification_service_pb2 as pb
        import notification_service_pb2_grpc as pb_grpc

        self._stub = pb_grpc.NotificationServiceStub(self._channel)
        self._pb = pb

    def notifier_admins(self, evenement: str, detail: str, entite_id: str = "") -> None:
        """
        Envoie une notification aux administrateurs via Notification Service.
        Dégradation gracieuse en cas d'erreur gRPC.
        """
        try:
            self._stub.NotifierAdmins(
                self._pb.NotifierAdminsRequest(
                    evenement=evenement,
                    detail=detail,
                    entite_id=entite_id,
                )
            )
            logger.info(
                "Admins notifiés — événement %s",
                evenement,
                extra={"evenement": evenement, "entite_id": entite_id},
            )
        except Exception as exc:
            logger.warning(
                "NotifierAdmins échoué — dégradation gracieuse",
                extra={"evenement": evenement, "error": str(exc)},
            )

    def envoyer_relance(self, facture_id: str, abonne_id: str, etape: int) -> None:
        """
        Déclenche l'envoi d'une relance WhatsApp pour une facture impayée.
        Dégradation gracieuse en cas d'erreur gRPC.
        """
        try:
            self._stub.EnvoyerRelance(
                self._pb.EnvoyerRelanceRequest(
                    facture_id=facture_id,
                    abonne_id=abonne_id,
                    etape=etape,
                )
            )
            logger.info(
                "Relance étape %d envoyée",
                etape,
                extra={"facture_id": facture_id, "abonne_id": abonne_id},
            )
        except grpc.RpcError as exc:
            logger.warning(
                "EnvoyerRelance échoué — dégradation gracieuse",
                extra={
                    "facture_id": facture_id,
                    "abonne_id": abonne_id,
                    "etape": etape,
                    "error": str(exc),
                },
            )
        except Exception as exc:
            logger.warning(
                "EnvoyerRelance erreur inattendue — dégradation gracieuse",
                extra={"error": str(exc)},
            )

    def envoyer_recu(
        self,
        paiement_id: str,
        facture_id: str,
        abonne_id: str,
        montant: float,
        solde_restant: float,
    ) -> None:
        """
        Déclenche l'envoi du reçu de paiement (PDF) à l'abonné via WhatsApp.
        Dégradation gracieuse : un échec ne remonte jamais — il ne doit pas
        faire échouer l'enregistrement du paiement.
        """
        try:
            self._stub.EnvoyerRecu(
                self._pb.EnvoyerRecuRequest(
                    paiement_id=paiement_id,
                    facture_id=facture_id,
                    abonne_id=abonne_id,
                    montant=montant,
                    solde_restant=solde_restant,
                )
            )
            logger.info(
                "Reçu de paiement envoyé",
                extra={"paiement_id": paiement_id, "facture_id": facture_id},
            )
        except grpc.RpcError as exc:
            logger.warning(
                "EnvoyerRecu échoué — dégradation gracieuse",
                extra={"paiement_id": paiement_id, "abonne_id": abonne_id, "error": str(exc)},
            )
        except Exception as exc:
            logger.warning(
                "EnvoyerRecu erreur inattendue — dégradation gracieuse",
                extra={"error": str(exc)},
            )


class AbonneServiceClient:
    """Client gRPC vers Abonné Service (port 50052)."""

    def __init__(self) -> None:
        address = f"{settings.ABONNE_GRPC_HOST}:{settings.ABONNE_GRPC_PORT}"
        self._channel = grpc.insecure_channel(address)
        _ensure_proto_in_syspath()

        import abonne_service_pb2 as pb
        import abonne_service_pb2_grpc as pb_grpc

        self._stub = pb_grpc.AbonneServiceStub(self._channel)
        self._pb = pb

    def suspendre_abonne(self, abonne_id: str) -> None:
        """
        Suspend un abonné dans Abonné Service.
        Dégradation gracieuse en cas d'erreur gRPC.
        """
        try:
            self._stub.SuspendreAbonne(self._pb.AbonneIdRequest(abonne_id=abonne_id))
            logger.info(
                "Abonné suspendu via Abonné Service",
                extra={"abonne_id": abonne_id},
            )
        except grpc.RpcError as exc:
            logger.warning(
                "SuspendreAbonne échoué — dégradation gracieuse",
                extra={"abonne_id": abonne_id, "error": str(exc)},
            )
        except Exception as exc:
            logger.warning(
                "SuspendreAbonne erreur inattendue — dégradation gracieuse",
                extra={"abonne_id": abonne_id, "error": str(exc)},
            )

    def reactiver_abonne(self, abonne_id: str) -> None:
        """
        Réactive un abonné suspendu dans Abonné Service après paiement complet.
        Dégradation gracieuse en cas d'erreur gRPC.
        """
        try:
            self._stub.ReactiverAbonne(self._pb.AbonneIdRequest(abonne_id=abonne_id))
            logger.info(
                "Abonné réactivé via Abonné Service",
                extra={"abonne_id": abonne_id},
            )
        except grpc.RpcError as exc:
            logger.warning(
                "ReactiverAbonne échoué — dégradation gracieuse",
                extra={"abonne_id": abonne_id, "error": str(exc)},
            )
        except Exception as exc:
            logger.warning(
                "ReactiverAbonne erreur inattendue — dégradation gracieuse",
                extra={"abonne_id": abonne_id, "error": str(exc)},
            )


class ConfigServiceClient:
    """Client gRPC vers Config Service (port 50058) — récupération des délais impayés."""

    def __init__(self) -> None:
        address = f"{settings.CONFIG_GRPC_HOST}:{settings.CONFIG_GRPC_PORT}"
        self._channel = grpc.insecure_channel(address)
        _ensure_proto_in_syspath()

        try:
            import config_service_pb2 as pb
            import config_service_pb2_grpc as pb_grpc

            self._stub = pb_grpc.ConfigServiceStub(self._channel)
            self._pb = pb
            self._available = True
        except Exception:
            self._available = False

    def get_delais_impayes(self) -> dict[str, object]:
        """
        Récupère les délais de relance depuis Config Service.
        Retourne les valeurs par défaut si le service est indisponible.
        """
        defaults: dict[str, object] = {
            "rappel_1": getattr(settings, "DEFAULT_DELAI_RAPPEL_1", 0),
            "rappel_2": getattr(settings, "DEFAULT_DELAI_RAPPEL_2", 3),
            "avertissement": getattr(settings, "DEFAULT_DELAI_AVERTISSEMENT", 7),
            "suspension": getattr(settings, "DEFAULT_DELAI_SUSPENSION", 10),
            "suspension_auto": getattr(settings, "DEFAULT_SUSPENSION_AUTO", True),
            "suspension_relances": getattr(settings, "DEFAULT_SUSPENSION_RELANCES", 5),
        }

        if not self._available:
            return defaults

        cles = [
            ("impaye_delai_rappel_1", "rappel_1", int),
            ("impaye_delai_rappel_2", "rappel_2", int),
            ("impaye_delai_avertissement", "avertissement", int),
            ("impaye_delai_suspension", "suspension", int),
            (
                "impaye_suspension_auto",
                "suspension_auto",
                lambda v: v.lower() == "true",
            ),
            ("impaye_suspension_relances", "suspension_relances", int),
        ]

        result = dict(defaults)
        for cle, key, converter in cles:
            try:
                response = self._stub.GetConfig(self._pb.ConfigKeyRequest(cle=cle))
                result[key] = converter(response.valeur)
            except grpc.RpcError:
                pass  # Valeur par défaut conservée
            except (ValueError, AttributeError):
                pass  # Valeur mal formée — défaut conservé

        return result


class ReportingServiceClient:
    """Client gRPC vers Reporting Service (port 50057) — stats de paiement (ADR-019).

    Read model aval : son indisponibilité ne doit jamais faire échouer un
    enregistrement de paiement. Dégradation gracieuse (log + retour False).
    """

    def __init__(self) -> None:
        address = f"{settings.REPORTING_GRPC_HOST}:{settings.REPORTING_GRPC_PORT}"
        self._channel = grpc.insecure_channel(address)
        _ensure_proto_in_syspath()

        import reporting_service_pb2 as pb
        import reporting_service_pb2_grpc as pb_grpc

        self._stub = pb_grpc.ReportingServiceStub(self._channel)
        self._pb = pb

    def update_stats_paiements(self, campagne_id: str, montant_paiement: float, type_update: str) -> bool:
        """type_update ∈ {PAIEMENT, IMPAYE_RESOLU}. Sans campagne_id, ne fait rien."""
        if not campagne_id:
            return False
        try:
            self._stub.UpdateStatsPaiements(
                self._pb.UpdateStatsPaiementsRequest(
                    campagne_id=campagne_id,
                    montant_paiement=montant_paiement,
                    type_update=type_update,
                )
            )
            return True
        except Exception as exc:
            logger.warning(
                "Reporting Service inaccessible — UpdateStatsPaiements ignoré",
                extra={"campagne_id": campagne_id, "type_update": type_update, "error": str(exc)},
            )
            return False
