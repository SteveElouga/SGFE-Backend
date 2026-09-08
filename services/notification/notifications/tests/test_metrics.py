"""Tests des métriques métier custom (OpenTelemetry) du Notification Service.

Vérifie que chaque compteur de `notifications/metrics.py` s'incrémente
réellement après l'action métier correspondante, en lisant les points de
données via un `InMemoryMetricReader` (pas de mock de l'API OTel elle-même).
"""

import uuid
from unittest.mock import MagicMock, patch

from django.test import TestCase
from opentelemetry import metrics
from opentelemetry.sdk.metrics import MeterProvider
from opentelemetry.sdk.metrics.export import InMemoryMetricReader, Sum

from notifications.models import Diffusion, DiffusionEnvoi, Envoi, StatutDiffusionEnvoi, StatutEnvoi, TypeEnvoi
from notifications.repositories import EnvoiRepository
from notifications.services import DiffusionService, EnvoiService
from notifications.whatsapp_client import WhatsAppDeliveryError

from .test_services import _make_abonne_mock, _make_facture_mock

_READER = InMemoryMetricReader()
# set_meter_provider ne peut être appelé qu'une fois par processus : au niveau
# module, pour être sûr que ce soit le premier (et unique) appel réel du run
# de tests de cette app. Les compteurs de `notifications/metrics.py`, créés à
# l'import (avant ce provider), sont reliés a posteriori grâce au mécanisme
# de proxy de l'API OTel.
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


class EnvoiMetricsTestCase(TestCase):
    """`sgfe.notification.envoi` — une tentative d'envoi WhatsApp réussie ou en échec."""

    @patch("notifications.services.whatsapp_client")
    @patch("notifications.services.config_client")
    @patch("notifications.services.abonne_client")
    @patch("notifications.services.facturation_client")
    def test_envoi_reussi_incremente_le_compteur_envoye(
        self, mock_fact: MagicMock, mock_abonne: MagicMock, mock_config: MagicMock, mock_wa: MagicMock
    ) -> None:
        facture_id = str(uuid.uuid4())
        abonne_id = str(uuid.uuid4())
        mock_fact.get_facture.return_value = _make_facture_mock(facture_id=facture_id, abonne_id=abonne_id)
        mock_fact.get_facture_pdf.return_value = (b"", "")
        mock_abonne.get_abonne.return_value = _make_abonne_mock(abonne_id=abonne_id)
        mock_config.get_token_validite_jours.return_value = 20
        mock_wa.send.return_value = None
        attributs = {"type_envoi": TypeEnvoi.FACTURE.value, "statut": StatutEnvoi.ENVOYE.value}

        avant = _valeur_compteur("sgfe.notification.envoi", attributs)
        EnvoiService().envoyer_facture(facture_id, abonne_id)
        apres = _valeur_compteur("sgfe.notification.envoi", attributs)

        self.assertEqual(apres - avant, 1.0)

    @patch("notifications.services.notifier_admins")
    @patch("notifications.services.whatsapp_client")
    @patch("notifications.services.config_client")
    @patch("notifications.services.abonne_client")
    @patch("notifications.services.facturation_client")
    def test_envoi_en_echec_incremente_le_compteur_echec(
        self,
        mock_fact: MagicMock,
        mock_abonne: MagicMock,
        mock_config: MagicMock,
        mock_wa: MagicMock,
        mock_notifier: MagicMock,
    ) -> None:
        facture_id = str(uuid.uuid4())
        abonne_id = str(uuid.uuid4())
        mock_fact.get_facture.return_value = _make_facture_mock(facture_id=facture_id, abonne_id=abonne_id)
        mock_fact.get_facture_pdf.return_value = (b"", "")
        mock_abonne.get_abonne.return_value = _make_abonne_mock(abonne_id=abonne_id)
        mock_config.get_token_validite_jours.return_value = 20
        mock_wa.send.side_effect = WhatsAppDeliveryError("Service inaccessible")
        attributs = {"type_envoi": TypeEnvoi.FACTURE.value, "statut": StatutEnvoi.ECHEC.value}

        avant = _valeur_compteur("sgfe.notification.envoi", attributs)
        EnvoiService().envoyer_facture(facture_id, abonne_id)
        apres = _valeur_compteur("sgfe.notification.envoi", attributs)

        self.assertEqual(apres - avant, 1.0)


class DiffusionEnvoiMetricsTestCase(TestCase):
    """`sgfe.notification.diffusion_envoi` — une ligne de diffusion traitée."""

    @patch("notifications.services.whatsapp_client")
    def test_ligne_envoyee_incremente_le_compteur(self, mock_wa: MagicMock) -> None:
        diffusion = Diffusion.objects.create(message="Annonce")
        DiffusionEnvoi.objects.create(diffusion=diffusion, abonne_id="a1", telephone="+237699000001")
        mock_wa.send.return_value = None
        attributs = {"statut": StatutDiffusionEnvoi.ENVOYE.value}

        avant = _valeur_compteur("sgfe.notification.diffusion_envoi", attributs)
        DiffusionService().traiter_lot_en_attente(10)
        apres = _valeur_compteur("sgfe.notification.diffusion_envoi", attributs)

        self.assertEqual(apres - avant, 1.0)

    @patch("notifications.services.whatsapp_client")
    def test_ligne_en_echec_incremente_le_compteur(self, mock_wa: MagicMock) -> None:
        diffusion = Diffusion.objects.create(message="Annonce")
        DiffusionEnvoi.objects.create(diffusion=diffusion, abonne_id="a1", telephone="+237699000001")
        mock_wa.send.side_effect = WhatsAppDeliveryError("service indisponible")
        attributs = {"statut": StatutDiffusionEnvoi.ECHEC.value}

        avant = _valeur_compteur("sgfe.notification.diffusion_envoi", attributs)
        DiffusionService().traiter_lot_en_attente(10)
        apres = _valeur_compteur("sgfe.notification.diffusion_envoi", attributs)

        self.assertEqual(apres - avant, 1.0)


class RetryMetricsTestCase(TestCase):
    """`sgfe.notification.retry` — une tentative de retry automatique."""

    def _creer_echec(self) -> Envoi:
        envoi = EnvoiRepository().create(
            facture_id=str(uuid.uuid4()),
            abonne_id=str(uuid.uuid4()),
            type_envoi=TypeEnvoi.FACTURE,
            telephone="+237699000001",
        )
        envoi.statut = StatutEnvoi.ECHEC
        envoi.tentatives = 1
        envoi.dernier_message = "Message original"
        envoi.save()
        return envoi

    @patch("notifications.services.whatsapp_client")
    def test_tentative_de_retry_incremente_le_compteur(self, mock_wa: MagicMock) -> None:
        self._creer_echec()
        mock_wa.send.return_value = None

        avant = _valeur_compteur("sgfe.notification.retry")
        EnvoiService().retenter_echecs(20)
        apres = _valeur_compteur("sgfe.notification.retry")

        self.assertEqual(apres - avant, 1.0)
