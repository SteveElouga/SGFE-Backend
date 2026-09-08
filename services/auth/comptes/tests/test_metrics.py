"""Tests des métriques métier custom (OpenTelemetry) du service Auth."""

from unittest.mock import MagicMock, patch

from django.test import TestCase
from opentelemetry import metrics
from opentelemetry.sdk.metrics import MeterProvider
from opentelemetry.sdk.metrics.export import InMemoryMetricReader, Sum

from comptes.models import Role, User
from comptes.services import AuthenticationError, AuthService, UserAdminService

_READER = InMemoryMetricReader()
# `set_meter_provider` ne peut être appelé qu'une fois par processus : posé au
# niveau module pour être le premier (et unique) appel réel du run de tests
# de cette app — les compteurs de `comptes/metrics.py`, créés à l'import via
# le meter « proxy » d'OTel, s'y relient automatiquement (voir metrics.py).
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


class AuthMetricsTestCase(TestCase):
    """Vérifie qu'une action d'authentification/gestion utilisateur incrémente le bon compteur."""

    def setUp(self) -> None:
        self.auth = AuthService()
        self.user_admin = UserAdminService()
        User.objects.create_user(
            username="comptable1",
            email="comptable1@example.com",
            password="secret123",
            role=Role.COMPTABLE,
            phone_number="+237690000001",
        )
        # WhatsApp mocké pour la création non-ADMIN (activation par OTP) —
        # même pattern que UserAdminServiceTests dans test_services.py.
        self.whatsapp_patcher = patch("comptes.services.whatsapp_client.send")
        self.mock_whatsapp: MagicMock = self.whatsapp_patcher.start()
        self.addCleanup(self.whatsapp_patcher.stop)

    def test_login_reussi_incremente_compteur_succes(self) -> None:
        avant = _valeur_compteur("sgfe.auth.connexion", {"resultat": "succes"})
        self.auth.login("comptable1", "secret123")
        apres = _valeur_compteur("sgfe.auth.connexion", {"resultat": "succes"})
        self.assertEqual(apres - avant, 1.0)

    def test_login_echoue_incremente_compteur_echec(self) -> None:
        avant = _valeur_compteur("sgfe.auth.connexion", {"resultat": "echec"})
        with self.assertRaises(AuthenticationError):
            self.auth.login("comptable1", "mauvais_mot_de_passe")
        apres = _valeur_compteur("sgfe.auth.connexion", {"resultat": "echec"})
        self.assertEqual(apres - avant, 1.0)

    def test_creation_utilisateur_incremente_compteur(self) -> None:
        avant = _valeur_compteur("sgfe.utilisateur.cree")
        self.user_admin.create_user(
            username="agent1",
            phone_number="+237690000099",
            role=Role.AGENT,
        )
        apres = _valeur_compteur("sgfe.utilisateur.cree")
        self.assertEqual(apres - avant, 1.0)

    def test_desactivation_utilisateur_incremente_compteur(self) -> None:
        user = self.user_admin.users.get_by_username("comptable1")
        avant = _valeur_compteur("sgfe.utilisateur.desactive")
        self.user_admin.deactivate_user(str(user.id))
        apres = _valeur_compteur("sgfe.utilisateur.desactive")
        self.assertEqual(apres - avant, 1.0)
