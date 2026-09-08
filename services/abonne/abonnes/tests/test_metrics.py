"""Tests des métriques métier custom (OpenTelemetry) du service Abonné."""

from django.test import TestCase
from opentelemetry import metrics
from opentelemetry.sdk.metrics import MeterProvider
from opentelemetry.sdk.metrics.export import InMemoryMetricReader, Sum

from abonnes.services import AbonneService, CompteurService

from .test_services import _create_abonne

_READER = InMemoryMetricReader()
# `set_meter_provider` ne peut être appelé qu'une fois par processus (le SDK
# ignore silencieusement tout appel suivant) : posé ici, au niveau module,
# pour être le premier — les compteurs de `abonnes/metrics.py`, créés avant
# ce provider réel (proxy OTel), s'y raccrochent automatiquement.
metrics.set_meter_provider(MeterProvider(metric_readers=[_READER]))


def _valeur_compteur(nom_metrique: str, attributs: dict[str, str] | None = None) -> float:
    """Additionne les points de données du compteur `nom_metrique` filtrés par `attributs`."""
    total = 0.0
    donnees = _READER.get_metrics_data()
    if donnees is None:
        return total
    for resource_metrics in donnees.resource_metrics:
        for scope_metrics in resource_metrics.scope_metrics:
            for metric in scope_metrics.metrics:
                if metric.name != nom_metrique or not isinstance(metric.data, Sum):
                    continue
                for point in metric.data.data_points:
                    point_attrs = dict(point.attributes) if point.attributes else {}
                    if attributs is None or all(point_attrs.get(cle) == valeur for cle, valeur in attributs.items()):
                        total += point.value
    return total


class AbonneMetricsTestCase(TestCase):
    """Vérifie qu'une action métier réussie incrémente bien le compteur correspondant."""

    def setUp(self) -> None:
        self.abonne_service = AbonneService()
        self.compteur_service = CompteurService()

    def test_creation_abonne_incremente_compteur(self) -> None:
        avant = _valeur_compteur("sgfe.abonne.cree")
        _create_abonne(self.abonne_service)
        apres = _valeur_compteur("sgfe.abonne.cree")
        self.assertEqual(apres - avant, 1.0)

    def test_suspension_abonne_incremente_compteur(self) -> None:
        abonne = _create_abonne(self.abonne_service)
        avant = _valeur_compteur("sgfe.abonne.suspendu")
        self.abonne_service.suspendre_abonne(str(abonne.id))
        apres = _valeur_compteur("sgfe.abonne.suspendu")
        self.assertEqual(apres - avant, 1.0)

    def test_reactivation_abonne_incremente_compteur(self) -> None:
        abonne = _create_abonne(self.abonne_service)
        self.abonne_service.suspendre_abonne(str(abonne.id))
        avant = _valeur_compteur("sgfe.abonne.reactive")
        self.abonne_service.reactiver_abonne(str(abonne.id))
        apres = _valeur_compteur("sgfe.abonne.reactive")
        self.assertEqual(apres - avant, 1.0)

    def test_resiliation_abonne_incremente_compteur(self) -> None:
        abonne = _create_abonne(self.abonne_service)
        avant = _valeur_compteur("sgfe.abonne.resilie")
        self.abonne_service.resilier_abonne(str(abonne.id))
        apres = _valeur_compteur("sgfe.abonne.resilie")
        self.assertEqual(apres - avant, 1.0)

    def test_remplacement_compteur_incremente_compteur(self) -> None:
        abonne = _create_abonne(self.abonne_service, index_initial=0)
        avant = _valeur_compteur("sgfe.abonne.compteur_remplace")
        self.compteur_service.remplacer_compteur(
            abonne_id=str(abonne.id),
            index_fermeture=120,
            nouveau_numero_compteur=2,
            nouveau_quartier="Nouveau Quartier",
            nouveau_camp=2,
            nouvel_index_initial=0,
            date_remplacement="2024-06-01",
        )
        apres = _valeur_compteur("sgfe.abonne.compteur_remplace")
        self.assertEqual(apres - avant, 1.0)
