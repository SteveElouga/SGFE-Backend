"""Tests des métriques métier custom (OpenTelemetry) du Paiement Service."""

from datetime import date, timedelta
from decimal import Decimal
from unittest.mock import MagicMock, patch

from django.test import TestCase
from opentelemetry import metrics
from opentelemetry.sdk.metrics import MeterProvider
from opentelemetry.sdk.metrics.export import InMemoryMetricReader, Sum

from paiements.models import ModePaiement, SoldeFacture
from paiements.repositories import SoldeFactureRepository
from paiements.services import ImpayeService, PaiementService

_READER = InMemoryMetricReader()
# `set_meter_provider` ne peut être appelé qu'une fois par processus : posé au
# niveau module pour être sûr que ce soit le premier (et unique) appel réel du
# run de tests de cette app. Les compteurs de `paiements/metrics.py`, créés à
# l'import (avant ce provider), y sont reliés a posteriori grâce au proxy
# interne de l'API OTel.
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


def _creer_solde(
    facture_id: str = "facture-001",
    abonne_id: str = "abonne-001",
    montant_total: float = 300.00,
    date_limite: date | None = None,
) -> SoldeFacture:
    """Crée un SoldeFacture de test via le repository (même helper que test_services.py)."""
    repo = SoldeFactureRepository()
    return repo.create(
        facture_id=facture_id,
        abonne_id=abonne_id,
        montant_total=Decimal(str(montant_total)),
        date_limite_paiement=date_limite or date(2026, 7, 1),
    )


class PaiementEnregistreMetricsTestCase(TestCase):
    """`sgfe.paiement.enregistre` et `sgfe.paiement.montant_encaisse`."""

    def setUp(self) -> None:
        self.svc = PaiementService()
        _creer_solde("facture-001", "abonne-001", 300.00)

    def test_enregistrement_paiement_incremente_compteur_et_montant(self) -> None:
        avant_compteur = _valeur_compteur("sgfe.paiement.enregistre", {"mode_paiement": "ESPECES"})
        avant_montant = _valeur_compteur("sgfe.paiement.montant_encaisse", {"mode_paiement": "ESPECES"})

        self.svc.enregistrer_paiement(
            facture_id="facture-001",
            abonne_id="abonne-001",
            montant=100.00,
            date_paiement=date(2026, 6, 20),
            mode_paiement=ModePaiement.ESPECES,
            reference_transaction="",
            enregistre_par="user-001",
        )

        apres_compteur = _valeur_compteur("sgfe.paiement.enregistre", {"mode_paiement": "ESPECES"})
        apres_montant = _valeur_compteur("sgfe.paiement.montant_encaisse", {"mode_paiement": "ESPECES"})
        self.assertEqual(apres_compteur - avant_compteur, 1.0)
        self.assertEqual(apres_montant - avant_montant, 100.0)

    def test_paiement_echoue_incremente_pas(self) -> None:
        """Un montant invalide (ValidationError) ne doit rien incrémenter."""
        from django.core.exceptions import ValidationError

        avant = _valeur_compteur("sgfe.paiement.enregistre", {"mode_paiement": "ESPECES"})
        with self.assertRaises(ValidationError):
            self.svc.enregistrer_paiement(
                facture_id="facture-001",
                abonne_id="abonne-001",
                montant=0.0,
                date_paiement=date(2026, 6, 20),
                mode_paiement=ModePaiement.ESPECES,
                reference_transaction="",
                enregistre_par="user-001",
            )
        apres = _valeur_compteur("sgfe.paiement.enregistre", {"mode_paiement": "ESPECES"})
        self.assertEqual(apres - avant, 0.0)


class PaiementAnnuleMetricsTestCase(TestCase):
    """`sgfe.paiement.annule`."""

    def setUp(self) -> None:
        self.svc = PaiementService()
        _creer_solde("facture-002", "abonne-002", 300.00)

    def test_annulation_paiement_incremente_compteur(self) -> None:
        paiement, _ = self.svc.enregistrer_paiement(
            facture_id="facture-002",
            abonne_id="abonne-002",
            montant=100.00,
            date_paiement=date(2026, 6, 20),
            mode_paiement=ModePaiement.ESPECES,
            reference_transaction="",
            enregistre_par="user-001",
        )

        avant = _valeur_compteur("sgfe.paiement.annule")
        self.svc.annuler_paiement(paiement_id=str(paiement.id), motif="erreur de saisie", annule_par="admin-1")
        apres = _valeur_compteur("sgfe.paiement.annule")
        self.assertEqual(apres - avant, 1.0)


ABONNE = "abonne-relance-metrics"

_DELAIS = {
    "rappel_1": 0,
    "rappel_2": 3,
    "avertissement": 7,
    "suspension": 10,
    "suspension_auto": True,
    "suspension_relances": 5,
}


@patch("paiements.services.NotificationServiceClient")
@patch("paiements.services.AbonneServiceClient")
@patch("paiements.services.ConfigServiceClient")
class RelanceEscaladeeMetricsTestCase(TestCase):
    """`sgfe.relance.escaladee`, une étape par test (label `etape` en chaîne)."""

    def _preparer(self, mock_config: MagicMock, mock_notif: MagicMock, mock_abonne: MagicMock) -> None:
        mock_config.return_value.get_delais_impayes.return_value = dict(_DELAIS)
        notif = MagicMock()
        notif.envoyer_relance.return_value = True
        mock_notif.return_value = notif
        mock_abonne.return_value = MagicMock()

    def test_etape_1_incremente_avec_le_bon_label(
        self, mock_config: MagicMock, mock_abonne: MagicMock, mock_notif: MagicMock
    ) -> None:
        self._preparer(mock_config, mock_notif, mock_abonne)
        SoldeFactureRepository().create(
            facture_id="facture-relance-1",
            abonne_id=ABONNE,
            montant_total=Decimal("10000"),
            # `list_impayes` exige une échéance strictement dépassée
            # (`date_limite_paiement__lt=today`) : 1 jour de retard, pas 0.
            date_limite_paiement=date.today() - timedelta(days=1),
            campagne_id="camp-1",
        )

        avant = _valeur_compteur("sgfe.relance.escaladee", {"etape": "1"})
        ImpayeService().verifier_et_escalader()
        apres = _valeur_compteur("sgfe.relance.escaladee", {"etape": "1"})
        self.assertEqual(apres - avant, 1.0)

    def test_etape_4_suspension_incremente_avec_le_bon_label(
        self, mock_config: MagicMock, mock_abonne: MagicMock, mock_notif: MagicMock
    ) -> None:
        self._preparer(mock_config, mock_notif, mock_abonne)
        SoldeFactureRepository().create(
            facture_id="facture-relance-4",
            abonne_id=ABONNE,
            montant_total=Decimal("10000"),
            date_limite_paiement=date.today() - timedelta(days=31),
            campagne_id="camp-1",
        )

        avant = _valeur_compteur("sgfe.relance.escaladee", {"etape": "4"})
        ImpayeService().verifier_et_escalader()
        apres = _valeur_compteur("sgfe.relance.escaladee", {"etape": "4"})
        self.assertEqual(apres - avant, 1.0)
