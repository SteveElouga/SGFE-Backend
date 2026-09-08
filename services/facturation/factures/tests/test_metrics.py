"""Tests des métriques métier custom (OpenTelemetry) du service Facturation."""

import datetime
import tempfile
from decimal import Decimal
from unittest.mock import patch

from django.test import TestCase
from opentelemetry import metrics
from opentelemetry.sdk.metrics import MeterProvider
from opentelemetry.sdk.metrics.export import InMemoryMetricReader, Sum

from factures.models import Facture, Tarif
from factures.pdf_generator import InfosSociete
from factures.services import FactureService, ReleveData, TarifService
from factures.tests.helpers import service_avec_clients_mockes

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


def _facture(**kw: object) -> Facture:
    defauts: dict[str, object] = dict(
        abonne_id="ab-metrics-1",
        campagne_id="camp-metrics-1",
        numero_facture=kw.pop("numero", "FACT-2026-09-9001"),
        ancien_index=Decimal("100"),
        nouveau_index=Decimal("120"),
        consommation=Decimal("20"),
        prix_m3=Decimal("500"),
        montant=Decimal("10000"),
        date_releve=datetime.date.today(),
        date_limite_paiement=datetime.date.today() + datetime.timedelta(days=15),
    )
    defauts.update(kw)
    return Facture.objects.create(**defauts)


class FactureMetricsTestCase(TestCase):
    """Vérifie qu'une action métier réussie incrémente bien le compteur correspondant."""

    def setUp(self) -> None:
        self.svc: FactureService = service_avec_clients_mockes()
        self.societe = InfosSociete(nom="SGFE Test", adresse="Yaoundé", telephone="+237000000000")

    def test_generation_facture_incremente_compteur_par_facture(self) -> None:
        """Un lot de 2 relevés générés doit incrémenter le compteur de 2, pas de 1."""
        avant = _valeur_compteur("sgfe.facture.generee")
        TarifService().update_tarif(Decimal("500.00"), datetime.date(2025, 7, 1))
        releves = [
            ReleveData(
                abonne_id="abo-metrics-1",
                ancien_index=100.0,
                nouveau_index=115.0,
                consommation=15.0,
                date_releve="2025-07-15",
            ),
            ReleveData(
                abonne_id="abo-metrics-2",
                ancien_index=200.0,
                nouveau_index=220.0,
                consommation=20.0,
                date_releve="2025-07-15",
            ),
        ]

        with tempfile.TemporaryDirectory() as tmpdir:
            with patch("factures.services.settings") as mock_settings:
                mock_settings.PDF_STORAGE_DIR = tmpdir
                mock_settings.DEFAULT_DELAI_PAIEMENT_JOURS = 5
                factures = self.svc.generer_factures(
                    campagne_id="camp-metrics-gen",
                    releves=releves,
                    delai_paiement_jours=5,
                    societe=self.societe,
                )

        apres = _valeur_compteur("sgfe.facture.generee")
        self.assertEqual(len(factures), 2)
        self.assertEqual(apres - avant, 2.0)

    def test_annulation_facture_incremente_compteur(self) -> None:
        avant = _valeur_compteur("sgfe.facture.annulee")
        f = _facture()
        self.svc.annuler_facture(str(f.id), motif="Erreur d'index", annule_par="admin")
        apres = _valeur_compteur("sgfe.facture.annulee")
        self.assertEqual(apres - avant, 1.0)

    def test_regeneration_facture_incremente_compteur_generee_et_annulee(self) -> None:
        """La régénération annule l'ancienne (compteur annulee) ET émet la
        corrigée (compteur regeneree) : les deux sont des événements distincts."""
        Tarif.objects.create(prix_m3=Decimal("500"), date_effet=datetime.date.today(), is_active=True)
        f = _facture(numero="FACT-2026-09-9002")
        self.svc._campagne_client.list_releves.return_value = [  # type: ignore[attr-defined]
            {
                "abonne_id": "ab-metrics-1",
                "ancien_index": 100.0,
                "nouveau_index": 130.0,
                "consommation": 30.0,
                "date_releve": datetime.date.today().isoformat(),
                "statut": "RELEVE",
            }
        ]

        avant_annulee = _valeur_compteur("sgfe.facture.annulee")
        avant_regeneree = _valeur_compteur("sgfe.facture.regeneree")
        self.svc.regenerer_facture(str(f.id), motif="Index corrigé", regenere_par="admin", delai_paiement_jours=15)
        apres_annulee = _valeur_compteur("sgfe.facture.annulee")
        apres_regeneree = _valeur_compteur("sgfe.facture.regeneree")

        self.assertEqual(apres_annulee - avant_annulee, 1.0)
        self.assertEqual(apres_regeneree - avant_regeneree, 1.0)
