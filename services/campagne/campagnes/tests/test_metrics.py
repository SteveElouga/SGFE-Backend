"""Tests des métriques métier custom (OpenTelemetry) du Campagne Service."""

from decimal import Decimal
from types import SimpleNamespace
from unittest.mock import patch

from django.test import TestCase
from opentelemetry import metrics
from opentelemetry.sdk.metrics import MeterProvider
from opentelemetry.sdk.metrics.export import InMemoryMetricReader, Sum

from campagnes.grpc_clients import AbonneServiceClient
from campagnes.models import StatutCampagne
from campagnes.repositories import CampagneRepository
from campagnes.services import CampagneService, ReleveService

_READER = InMemoryMetricReader()
# set_meter_provider ne peut être appelé qu'une fois par processus : posé au
# niveau module pour être sûr que ce soit le premier (et unique) appel réel
# du run de tests de cette app — les compteurs de `campagnes/metrics.py`,
# créés avant ce point via le proxy OTel, y sont raccordés rétroactivement.
metrics.set_meter_provider(MeterProvider(metric_readers=[_READER]))

# ajouter_abonne_campagne vérifie le statut ACTIF de l'abonné via un appel
# gRPC réel à Abonné Service (ANO-003) — patché pour tout le module, comme
# dans test_services.py, pour ne pas dépendre d'un Abonné Service démarré.
_abonne_patcher = patch.object(AbonneServiceClient, "get_abonne", return_value=SimpleNamespace(statut="ACTIF"))


def setUpModule() -> None:
    _abonne_patcher.start()


def tearDownModule() -> None:
    _abonne_patcher.stop()


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


class CampagneMetricsTestCase(TestCase):
    """Vérifie qu'une action métier réussie incrémente bien le compteur correspondant.

    Les compteurs sont cumulatifs (jamais de reset entre tests) : chaque test
    compare une valeur avant/après sa propre action, jamais une valeur absolue.
    """

    def setUp(self) -> None:
        self.campagne_svc = CampagneService()
        self.releve_svc = ReleveService()

    def test_demarrer_campagne_incremente_compteur(self) -> None:
        campagne = self.campagne_svc.creer_campagne(
            nom="Campagne Démarrage", periode_mois=6, periode_annee=2026, created_by="user-A"
        )
        avant = _valeur_compteur("sgfe.campagne.demarree")
        self.campagne_svc.demarrer_campagne(str(campagne.id))
        apres = _valeur_compteur("sgfe.campagne.demarree")
        self.assertEqual(apres - avant, 1.0)

    def test_demarrage_automatique_planifie_incremente_compteur(self) -> None:
        from datetime import date

        self.campagne_svc.creer_campagne(
            nom="Campagne Planifiée Auto",
            periode_mois=6,
            periode_annee=2026,
            created_by="user-A",
            date_planifiee=str(date.today()),
        )
        avant = _valeur_compteur("sgfe.campagne.demarree")
        self.campagne_svc.demarrer_campagnes_planifiees_pour_aujourd_hui()
        apres = _valeur_compteur("sgfe.campagne.demarree")
        self.assertEqual(apres - avant, 1.0)

    def test_cloturer_campagne_incremente_compteur(self) -> None:
        campagne = self.campagne_svc.creer_campagne(
            nom="Campagne Clôture", periode_mois=6, periode_annee=2026, created_by="user-A"
        )
        CampagneRepository().update_statut(campagne, StatutCampagne.EN_COURS)
        avant = _valeur_compteur("sgfe.campagne.cloturee")
        self.campagne_svc.cloturer_campagne(str(campagne.id))
        apres = _valeur_compteur("sgfe.campagne.cloturee")
        self.assertEqual(apres - avant, 1.0)

    def test_saisir_index_incremente_compteur(self) -> None:
        campagne = self.campagne_svc.creer_campagne(
            nom="Campagne Saisie", periode_mois=6, periode_annee=2026, created_by="user-A"
        )
        CampagneRepository().update_statut(campagne, StatutCampagne.EN_COURS)
        releve = self.campagne_svc.ajouter_abonne_campagne(str(campagne.id), "abonne-001", ancien_index=Decimal("100"))
        avant = _valeur_compteur("sgfe.releve.saisi")
        self.releve_svc.saisir_index(str(releve.id), nouveau_index=Decimal("150"), agent_id="agent-001")
        apres = _valeur_compteur("sgfe.releve.saisi")
        self.assertEqual(apres - avant, 1.0)

    def test_corriger_releve_incremente_compteur(self) -> None:
        campagne = self.campagne_svc.creer_campagne(
            nom="Campagne Correction", periode_mois=6, periode_annee=2026, created_by="user-A"
        )
        CampagneRepository().update_statut(campagne, StatutCampagne.EN_COURS)
        releve = self.campagne_svc.ajouter_abonne_campagne(str(campagne.id), "abonne-001", ancien_index=Decimal("100"))
        self.releve_svc.saisir_index(str(releve.id), nouveau_index=Decimal("150"), agent_id="agent-001")
        avant = _valeur_compteur("sgfe.releve.corrige")
        self.releve_svc.corriger_releve(str(releve.id), nouveau_index=Decimal("180"), auteur_id="admin-001")
        apres = _valeur_compteur("sgfe.releve.corrige")
        self.assertEqual(apres - avant, 1.0)
