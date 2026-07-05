"""Fabriques partagées pour les tests du Facturation Service."""

from unittest.mock import MagicMock

from factures.services import FactureService


def service_avec_clients_mockes() -> FactureService:
    """FactureService avec ses clients gRPC mockés — tests isolés, sans réseau.

    Repli identité/campagne indisponibles (get_abonne=None, get_campagne_nom="")
    pour reproduire le comportement dégradé attendu sans appel réseau réel.
    """
    svc = FactureService(
        paiement_client=MagicMock(),
        notification_client=MagicMock(),
        abonne_client=MagicMock(),
        campagne_client=MagicMock(),
        reporting_client=MagicMock(),
    )
    svc._abonne_client.get_abonne.return_value = None
    svc._campagne_client.get_campagne_nom.return_value = ""
    svc._notification_client.get_espace_url.return_value = ("", "")
    return svc
