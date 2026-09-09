"""RGPD — droit à l'effacement, propagé depuis Abonné Service.

Tests d'intégration (base réelle — Postgres en CI/FORCE_POSTGRES_TESTS,
SQLite en local, voir settings.py) de `EnvoiService.anonymiser_envois_abonne`
et `DiffusionService.anonymiser_envois_abonne` : les seuls mécanismes
d'anonymisation RGPD de ce service (voir docs/RGPD_PERIMETRE_EFFACEMENT.md).

Seul `abonne_client` (dépendance externe, gRPC) est mocké — tout le reste
(création des `Envoi`/`DiffusionEnvoi`, appel du service, relecture depuis la
base) passe par le moteur de base de données réellement configuré, comme le
reste de la suite (voir `notifications/tests/test_diffusion.py`).
"""

import uuid
from unittest.mock import MagicMock, patch

import grpc
from django.test import TestCase

from notifications.models import Diffusion, DiffusionEnvoi, Envoi, StatutEnvoi, TypeEnvoi
from notifications.services import DiffusionService, EnvoiService


def _abonne_mock(statut: str = "RESILIE") -> MagicMock:
    mock = MagicMock()
    mock.statut = statut
    return mock


def _create_envoi(
    abonne_id: str,
    telephone: str = "+237699000001",
    dernier_message: str = "",
    facture_id: str = "",
    statut: str = StatutEnvoi.ENVOYE,
    tentatives: int = 1,
) -> Envoi:
    return Envoi.objects.create(
        facture_id=facture_id or str(uuid.uuid4()),
        abonne_id=abonne_id,
        type_envoi=TypeEnvoi.FACTURE,
        telephone=telephone,
        dernier_message=dernier_message,
        statut=statut,
        tentatives=tentatives,
    )


class TestEnvoiServiceAnonymiserEnvoisAbonne(TestCase):
    """`EnvoiService.anonymiser_envois_abonne` — Envoi.telephone / dernier_message."""

    @patch("notifications.services.abonne_client")
    def test_abonne_actif_raises(self, mock_abonne: MagicMock) -> None:
        mock_abonne.get_abonne.return_value = _abonne_mock(statut="ACTIF")
        abonne_id = str(uuid.uuid4())
        _create_envoi(abonne_id)

        with self.assertRaises(ValueError):
            EnvoiService().anonymiser_envois_abonne(abonne_id)

    @patch("notifications.services.abonne_client")
    def test_abonne_suspendu_raises(self, mock_abonne: MagicMock) -> None:
        mock_abonne.get_abonne.return_value = _abonne_mock(statut="SUSPENDU")
        abonne_id = str(uuid.uuid4())
        _create_envoi(abonne_id)

        with self.assertRaises(ValueError):
            EnvoiService().anonymiser_envois_abonne(abonne_id)

    @patch("notifications.services.abonne_client")
    def test_abonne_service_injoignable_raises(self, mock_abonne: MagicMock) -> None:
        mock_abonne.get_abonne.side_effect = grpc.RpcError("Abonné Service injoignable")
        abonne_id = str(uuid.uuid4())
        _create_envoi(abonne_id)

        with self.assertRaises(ValueError):
            EnvoiService().anonymiser_envois_abonne(abonne_id)

    @patch("notifications.services.abonne_client")
    def test_resilie_remplace_telephone_et_dernier_message(self, mock_abonne: MagicMock) -> None:
        mock_abonne.get_abonne.return_value = _abonne_mock(statut="RESILIE")
        abonne_id = str(uuid.uuid4())
        envoi = _create_envoi(
            abonne_id,
            telephone="+237699000001",
            dernier_message="Bonjour Jean DUPONT, votre facture de Juillet 2025 s'élève à 7500 FCFA.",
        )

        nb = EnvoiService().anonymiser_envois_abonne(abonne_id)

        self.assertEqual(nb, 1)
        envoi.refresh_from_db()
        self.assertEqual(envoi.telephone, EnvoiService.TELEPHONE_ANONYMISE)
        self.assertEqual(envoi.dernier_message, EnvoiService.DERNIER_MESSAGE_ANONYMISE)
        self.assertNotIn("DUPONT", envoi.dernier_message)

    @patch("notifications.services.abonne_client")
    def test_resilie_ignore_dernier_message_vide(self, mock_abonne: MagicMock) -> None:
        """Un Envoi jamais tenté (dernier_message vide) reste vide — pas de
        placeholder là où il n'y avait rien à effacer."""
        mock_abonne.get_abonne.return_value = _abonne_mock(statut="RESILIE")
        abonne_id = str(uuid.uuid4())
        envoi = _create_envoi(abonne_id, dernier_message="")

        EnvoiService().anonymiser_envois_abonne(abonne_id)

        envoi.refresh_from_db()
        self.assertEqual(envoi.dernier_message, "")
        self.assertEqual(envoi.telephone, EnvoiService.TELEPHONE_ANONYMISE)

    @patch("notifications.services.abonne_client")
    def test_preserve_les_champs_non_pii(self, mock_abonne: MagicMock) -> None:
        """facture_id/paiement_id/type_envoi/statut/tentatives/created_at ne
        sont pas des PII propres à cet abonné — préservés intacts (historique
        d'envoi exploitable pour le support, voir la docstring de la méthode)."""
        mock_abonne.get_abonne.return_value = _abonne_mock(statut="RESILIE")
        abonne_id = str(uuid.uuid4())
        facture_id = str(uuid.uuid4())
        envoi = _create_envoi(abonne_id, facture_id=facture_id, statut=StatutEnvoi.ECHEC, tentatives=3)
        created_at_avant = envoi.created_at

        EnvoiService().anonymiser_envois_abonne(abonne_id)

        envoi.refresh_from_db()
        self.assertEqual(envoi.facture_id, facture_id)
        self.assertEqual(envoi.abonne_id, abonne_id)
        self.assertEqual(envoi.type_envoi, TypeEnvoi.FACTURE)
        self.assertEqual(envoi.statut, StatutEnvoi.ECHEC)
        self.assertEqual(envoi.tentatives, 3)
        self.assertEqual(envoi.created_at, created_at_avant)

    @patch("notifications.services.abonne_client")
    def test_anonymise_tous_les_envois_de_l_abonne(self, mock_abonne: MagicMock) -> None:
        mock_abonne.get_abonne.return_value = _abonne_mock(statut="RESILIE")
        abonne_id = str(uuid.uuid4())
        autre_abonne_id = str(uuid.uuid4())
        _create_envoi(abonne_id, dernier_message="Message 1")
        _create_envoi(abonne_id, dernier_message="Message 2")
        autre_envoi = _create_envoi(autre_abonne_id, telephone="+237699000099", dernier_message="Ne pas toucher")

        nb = EnvoiService().anonymiser_envois_abonne(abonne_id)

        self.assertEqual(nb, 2)
        for envoi in Envoi.objects.filter(abonne_id=abonne_id):
            self.assertEqual(envoi.telephone, EnvoiService.TELEPHONE_ANONYMISE)
            self.assertEqual(envoi.dernier_message, EnvoiService.DERNIER_MESSAGE_ANONYMISE)
        autre_envoi.refresh_from_db()
        self.assertEqual(autre_envoi.telephone, "+237699000099")
        self.assertEqual(autre_envoi.dernier_message, "Ne pas toucher")

    @patch("notifications.services.abonne_client")
    def test_est_idempotent(self, mock_abonne: MagicMock) -> None:
        mock_abonne.get_abonne.return_value = _abonne_mock(statut="RESILIE")
        abonne_id = str(uuid.uuid4())
        _create_envoi(abonne_id, dernier_message="Bonjour Jean")

        premier = EnvoiService().anonymiser_envois_abonne(abonne_id)
        second = EnvoiService().anonymiser_envois_abonne(abonne_id)

        self.assertEqual(premier, 1)
        self.assertEqual(second, 1)
        envoi = Envoi.objects.get(abonne_id=abonne_id)
        self.assertEqual(envoi.telephone, EnvoiService.TELEPHONE_ANONYMISE)
        self.assertEqual(envoi.dernier_message, EnvoiService.DERNIER_MESSAGE_ANONYMISE)

    @patch("notifications.services.abonne_client")
    def test_aucun_envoi_retourne_zero(self, mock_abonne: MagicMock) -> None:
        mock_abonne.get_abonne.return_value = _abonne_mock(statut="RESILIE")
        self.assertEqual(EnvoiService().anonymiser_envois_abonne(str(uuid.uuid4())), 0)


class TestDiffusionServiceAnonymiserEnvoisAbonne(TestCase):
    """`DiffusionService.anonymiser_envois_abonne` — DiffusionEnvoi.telephone."""

    def _create_diffusion_envoi(self, abonne_id: str, telephone: str = "+237699000001") -> DiffusionEnvoi:
        diffusion = Diffusion.objects.create(message="Coupure d'eau demain 8h-12h")
        return DiffusionEnvoi.objects.create(diffusion=diffusion, abonne_id=abonne_id, telephone=telephone)

    @patch("notifications.services.abonne_client")
    def test_abonne_actif_raises(self, mock_abonne: MagicMock) -> None:
        mock_abonne.get_abonne.return_value = _abonne_mock(statut="ACTIF")
        abonne_id = str(uuid.uuid4())
        self._create_diffusion_envoi(abonne_id)

        with self.assertRaises(ValueError):
            DiffusionService().anonymiser_envois_abonne(abonne_id)

    @patch("notifications.services.abonne_client")
    def test_abonne_service_injoignable_raises(self, mock_abonne: MagicMock) -> None:
        mock_abonne.get_abonne.side_effect = grpc.RpcError("Abonné Service injoignable")
        abonne_id = str(uuid.uuid4())
        self._create_diffusion_envoi(abonne_id)

        with self.assertRaises(ValueError):
            DiffusionService().anonymiser_envois_abonne(abonne_id)

    @patch("notifications.services.abonne_client")
    def test_resilie_remplace_le_telephone(self, mock_abonne: MagicMock) -> None:
        mock_abonne.get_abonne.return_value = _abonne_mock(statut="RESILIE")
        abonne_id = str(uuid.uuid4())
        envoi = self._create_diffusion_envoi(abonne_id, telephone="+237699000001")

        nb = DiffusionService().anonymiser_envois_abonne(abonne_id)

        self.assertEqual(nb, 1)
        envoi.refresh_from_db()
        self.assertEqual(envoi.telephone, EnvoiService.TELEPHONE_ANONYMISE)

    @patch("notifications.services.abonne_client")
    def test_preserve_le_message_de_diffusion_et_le_statut(self, mock_abonne: MagicMock) -> None:
        """`Diffusion.message` est un texte libre commun à TOUS les
        destinataires visés (jamais personnalisé) — rien à y anonymiser pour
        un abonné précis (voir la docstring de la méthode)."""
        mock_abonne.get_abonne.return_value = _abonne_mock(statut="RESILIE")
        abonne_id = str(uuid.uuid4())
        envoi = self._create_diffusion_envoi(abonne_id)
        message_avant = envoi.diffusion.message
        statut_avant = envoi.statut

        DiffusionService().anonymiser_envois_abonne(abonne_id)

        envoi.refresh_from_db()
        envoi.diffusion.refresh_from_db()
        self.assertEqual(envoi.diffusion.message, message_avant)
        self.assertEqual(envoi.statut, statut_avant)
        self.assertEqual(envoi.abonne_id, abonne_id)

    @patch("notifications.services.abonne_client")
    def test_est_idempotent(self, mock_abonne: MagicMock) -> None:
        mock_abonne.get_abonne.return_value = _abonne_mock(statut="RESILIE")
        abonne_id = str(uuid.uuid4())
        self._create_diffusion_envoi(abonne_id)

        premier = DiffusionService().anonymiser_envois_abonne(abonne_id)
        second = DiffusionService().anonymiser_envois_abonne(abonne_id)

        self.assertEqual(premier, 1)
        self.assertEqual(second, 1)
