"""Export RGPD structuré des données d'un abonné (droit à la portabilité).

Agrège, pour un `abonne_id` donné :
  - son identité et ses coordonnées, DÉCHIFFRÉES (`abonnes/fields.py` les
    déchiffre déjà de façon transparente à la lecture — c'est ici le sujet
    des données lui-même qui les demande, pas un tiers) ;
  - ses compteurs (actif + historique de remplacement) — natifs à ce service,
    aucun appel gRPC nécessaire ;
  - ses relevés (Campagne Service), factures (Facturation Service), paiements
    (Paiement Service) et envois WhatsApp l'ayant ciblé (Notification
    Service) — chacun via le client gRPC correspondant (`grpc_clients.py`).

Dégradation gracieuse SECTION PAR SECTION : l'indisponibilité d'un service
externe ne fait jamais échouer l'export dans son ensemble. La section
correspondante documente `"disponible": False` et la raison plutôt que de
faire échouer toute la demande d'un sujet de données pour une panne
partielle — le reste de l'export (et notamment l'identité, native à ce
service) reste utilisable.

Diffusions WhatsApp — volontairement absentes, documentées comme telles
directement dans l'export plutôt que silencieusement omises : voir
`grpc_clients.NotificationServiceClient` pour le détail (aucun RPC ne permet
de savoir quelles diffusions ont visé un abonné précis aujourd'hui).

Format et volume (choix documenté) : un objet JSON unique, renvoyé
synchrone. Pour UN abonné, le volume total (compteurs + historique + relevés
+ factures + paiements + envois) reste de l'ordre de quelques centaines de
lignes au grand maximum sur toute la durée d'un abonnement — largement sous
la limite par défaut d'un message gRPC (4 Mo). Pas besoin ici du mécanisme
de référence/job asynchrone qu'imposerait un export à l'échelle du système.
"""

from __future__ import annotations

import json
import logging
from datetime import UTC, datetime
from typing import Any, Callable

from abonnes.grpc_clients import (
    CampagneServiceClient,
    FacturationServiceClient,
    NotificationServiceClient,
    PaiementServiceClient,
)
from abonnes.models import Compteur
from abonnes.repositories import AbonneRepository, CompteurRepository, HistoriqueCompteurRepository
from abonnes.serializers import compteur_to_response, historique_to_response

logger = logging.getLogger(__name__)

# Non exposé aujourd'hui : `NotificationService.ListDiffusions` ne renvoie que
# des compteurs agrégés par diffusion (nb_total/nb_envoyes/nb_echecs) — aucun
# RPC ne liste les abonnés visés par une diffusion donnée (`DiffusionEnvoi`
# n'est exposé par aucune méthode du proto). Documenté plutôt qu'un RPC
# inventé pour l'occasion (voir proto/notification_service.proto).
_RAISON_DIFFUSIONS_NON_EXPOSEES = (
    "Non exposé aujourd'hui : NotificationService.ListDiffusions ne renvoie que des "
    "compteurs agrégés par diffusion, aucun RPC ne liste les abonnés individuellement "
    "ciblés par une diffusion donnée (voir proto/notification_service.proto)."
)


class ExportService:
    """Construit l'export RGPD structuré d'un abonné."""

    def __init__(
        self,
        campagne_client: CampagneServiceClient | None = None,
        facturation_client: FacturationServiceClient | None = None,
        paiement_client: PaiementServiceClient | None = None,
        notification_client: NotificationServiceClient | None = None,
    ) -> None:
        self._abonnes = AbonneRepository()
        self._compteurs = CompteurRepository()
        self._historique = HistoriqueCompteurRepository()
        # Résolus ici (pas à l'appel) : la construction d'un client gRPC est
        # sans I/O (canal paresseux) — voir grpc_clients.py — et les tests
        # peuvent ensuite monkey-patcher une méthode précise sur l'instance
        # déjà créée, comme le reste du dépôt le fait (ex. FactureService).
        self._campagne_client = campagne_client or CampagneServiceClient()
        self._facturation_client = facturation_client or FacturationServiceClient()
        self._paiement_client = paiement_client or PaiementServiceClient()
        self._notification_client = notification_client or NotificationServiceClient()

    def _section(self, nom: str, fn: Callable[[], Any]) -> dict[str, Any]:
        """Exécute `fn`, ou dégrade gracieusement la section en cas d'échec."""
        try:
            return {"disponible": True, "donnees": fn()}
        except Exception as exc:  # dégradation gracieuse assumée — voir docstring module
            logger.warning("Export RGPD — section %s indisponible : %s", nom, exc)
            return {"disponible": False, "raison": str(exc)}

    def exporter(self, abonne_id: str) -> dict[str, Any]:
        """Construit l'export complet. Lève `Abonne.DoesNotExist` si l'abonné
        n'existe pas — sans identité, il n'y a rien à exporter, contrairement
        aux sections externes qui, elles, dégradent gracieusement."""
        abonne = self._abonnes.get_by_id(abonne_id)

        try:
            compteur_actif = self._compteurs.get_actif(abonne_id)
        except Compteur.DoesNotExist:
            compteur_actif = None
        historique = self._historique.list_by_abonne(abonne_id)

        return {
            "genere_le": datetime.now(UTC).isoformat(),
            "abonne_id": str(abonne.id),
            "identite": {
                "numero_abonne": abonne.numero_abonne,
                "nom": abonne.nom,
                "prenom": abonne.prenom,
                "telephone_whatsapp": abonne.telephone_whatsapp,
                "adresse": abonne.adresse,
                "statut": abonne.statut,
                "date_creation": abonne.created_at.isoformat(),
            },
            "compteurs": {
                "actif": compteur_to_response(compteur_actif) if compteur_actif else None,
                "historique_remplacements": [historique_to_response(h) for h in historique],
            },
            "releves": self._section(
                "releves (campagne-service)", lambda: self._campagne_client.list_releves_abonne(abonne_id)
            ),
            "factures": self._section(
                "factures (facturation-service)", lambda: self._facturation_client.list_factures_abonne(abonne_id)
            ),
            "paiements": self._section(
                "paiements (paiement-service)", lambda: self._paiement_client.list_paiements_abonne(abonne_id)
            ),
            "envois_whatsapp": self._section(
                "envois (notification-service)", lambda: self._notification_client.list_envois_abonne(abonne_id)
            ),
            "diffusions_whatsapp": {
                "disponible": False,
                "raison": _RAISON_DIFFUSIONS_NON_EXPOSEES,
            },
        }


def exporter_donnees_abonne(abonne_id: str, **clients: Any) -> dict[str, Any]:
    """Point d'entrée fonctionnel, partagé par la commande de management et
    le servicer gRPC (`ExporterDonneesAbonne`)."""
    return ExportService(**clients).exporter(abonne_id)


def exporter_donnees_abonne_json(abonne_id: str, **clients: Any) -> str:
    """Même export, sérialisé en JSON structuré et lisible (indenté)."""
    return json.dumps(exporter_donnees_abonne(abonne_id, **clients), ensure_ascii=False, indent=2)
