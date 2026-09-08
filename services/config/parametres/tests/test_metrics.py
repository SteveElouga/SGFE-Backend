"""Tests des métriques métier custom (OpenTelemetry) du service Config."""

from django.test import TestCase
from opentelemetry import metrics
from opentelemetry.sdk.metrics import MeterProvider
from opentelemetry.sdk.metrics.export import InMemoryMetricReader, Sum

from parametres.services import ConfigService

_READER = InMemoryMetricReader()
# set_meter_provider ne peut être appelé qu'une fois par processus : au niveau
# module, pour être sûr que ce soit le premier (et unique) appel réel du run
# de tests de cette app.
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


class ConfigMetricsTestCase(TestCase):
    """Vérifie qu'une modification de paramètre réussie incrémente bien le compteur."""

    def setUp(self) -> None:
        self.service = ConfigService()

    def test_modification_parametre_incremente_compteur(self) -> None:
        avant = _valeur_compteur("sgfe.config.modifie", {"cle": "delai_paiement_jours"})
        self.service.update("delai_paiement_jours", "7")
        apres = _valeur_compteur("sgfe.config.modifie", {"cle": "delai_paiement_jours"})
        self.assertEqual(apres - avant, 1.0)

    def test_modification_parametre_distingue_les_cles(self) -> None:
        avant = _valeur_compteur("sgfe.config.modifie", {"cle": "token_validite_jours"})
        self.service.update("token_validite_jours", "30")
        apres = _valeur_compteur("sgfe.config.modifie", {"cle": "token_validite_jours"})
        self.assertEqual(apres - avant, 1.0)
