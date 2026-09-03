"""Tests de DiffusionService — création, envoi par lot, agrégation des compteurs."""

import uuid
from unittest.mock import MagicMock, patch

from django.test import TestCase

from notifications.models import Diffusion, DiffusionEnvoi, StatutDiffusion, StatutDiffusionEnvoi
from notifications.services import DiffusionService
from notifications.whatsapp_client import WhatsAppDeliveryError


def _abonne_mock(abonne_id: str, telephone: str = "+237699000001") -> MagicMock:
    mock = MagicMock()
    mock.abonne_id = abonne_id
    mock.telephone_whatsapp = telephone
    return mock


class TestCreerDiffusion(TestCase):
    @patch("notifications.services.abonne_client")
    def test_cree_une_ligne_par_abonne_resolu(self, mock_abonne: MagicMock) -> None:
        aid1, aid2 = str(uuid.uuid4()), str(uuid.uuid4())
        mock_abonne.get_abonne.side_effect = lambda aid: _abonne_mock(aid, "+237699000001")

        diffusion = DiffusionService().creer_diffusion("Coupure d'eau demain", [aid1, aid2], created_by="admin-1")

        self.assertEqual(diffusion.message, "Coupure d'eau demain")
        self.assertEqual(diffusion.created_by, "admin-1")
        self.assertEqual(diffusion.statut, StatutDiffusion.EN_COURS)
        self.assertEqual(DiffusionEnvoi.objects.filter(diffusion=diffusion).count(), 2)
        self.assertTrue(
            DiffusionEnvoi.objects.filter(diffusion=diffusion, statut=StatutDiffusionEnvoi.EN_ATTENTE).exists()
        )

    @patch("notifications.services.abonne_client")
    def test_un_abonne_introuvable_n_empeche_pas_les_autres(self, mock_abonne: MagicMock) -> None:
        """Dégradation par abonné, pas par diffusion entière."""
        import grpc

        aid_ok, aid_ko = str(uuid.uuid4()), str(uuid.uuid4())

        def _get_abonne(aid: str) -> MagicMock:
            if aid == aid_ko:
                raise grpc.RpcError("introuvable")
            return _abonne_mock(aid)

        mock_abonne.get_abonne.side_effect = _get_abonne

        diffusion = DiffusionService().creer_diffusion("Message", [aid_ok, aid_ko], created_by="admin-1")

        self.assertEqual(DiffusionEnvoi.objects.filter(diffusion=diffusion).count(), 1)
        self.assertEqual(DiffusionEnvoi.objects.get(diffusion=diffusion).abonne_id, aid_ok)

    @patch("notifications.services.abonne_client")
    def test_aucun_abonne_resolu_cree_une_diffusion_vide(self, mock_abonne: MagicMock) -> None:
        import grpc

        mock_abonne.get_abonne.side_effect = grpc.RpcError("introuvable")

        diffusion = DiffusionService().creer_diffusion("Message", [str(uuid.uuid4())], created_by="admin-1")

        self.assertEqual(DiffusionEnvoi.objects.filter(diffusion=diffusion).count(), 0)


class TestCompter(TestCase):
    def test_agrege_les_statuts_des_envois(self) -> None:
        diffusion = Diffusion.objects.create(message="M")
        DiffusionEnvoi.objects.create(
            diffusion=diffusion, abonne_id="a1", telephone="+1", statut=StatutDiffusionEnvoi.ENVOYE
        )
        DiffusionEnvoi.objects.create(
            diffusion=diffusion, abonne_id="a2", telephone="+2", statut=StatutDiffusionEnvoi.ENVOYE
        )
        DiffusionEnvoi.objects.create(
            diffusion=diffusion, abonne_id="a3", telephone="+3", statut=StatutDiffusionEnvoi.ECHEC
        )
        DiffusionEnvoi.objects.create(
            diffusion=diffusion, abonne_id="a4", telephone="+4", statut=StatutDiffusionEnvoi.EN_ATTENTE
        )

        nb_total, nb_envoyes, nb_echecs = DiffusionService().compter(diffusion)

        self.assertEqual((nb_total, nb_envoyes, nb_echecs), (4, 2, 1))


class TestTraiterLotEnAttente(TestCase):
    @patch("notifications.services.whatsapp_client")
    def test_envoie_le_lot_et_met_a_jour_les_statuts(self, mock_wa: MagicMock) -> None:
        diffusion = Diffusion.objects.create(message="Annonce")
        DiffusionEnvoi.objects.create(diffusion=diffusion, abonne_id="a1", telephone="+237699000001")
        DiffusionEnvoi.objects.create(diffusion=diffusion, abonne_id="a2", telephone="+237699000002")
        mock_wa.send.return_value = None

        touched = DiffusionService().traiter_lot_en_attente(10)

        self.assertEqual(mock_wa.send.call_count, 2)
        self.assertEqual(
            set(DiffusionEnvoi.objects.filter(diffusion=diffusion).values_list("statut", flat=True)),
            {StatutDiffusionEnvoi.ENVOYE},
        )
        self.assertIn(str(diffusion.id), touched)

    @patch("notifications.services.whatsapp_client")
    def test_echec_whatsapp_marque_la_ligne_echec(self, mock_wa: MagicMock) -> None:
        diffusion = Diffusion.objects.create(message="Annonce")
        DiffusionEnvoi.objects.create(diffusion=diffusion, abonne_id="a1", telephone="+237699000001")
        mock_wa.send.side_effect = WhatsAppDeliveryError("service indisponible")

        DiffusionService().traiter_lot_en_attente(10)

        envoi = DiffusionEnvoi.objects.get(diffusion=diffusion)
        self.assertEqual(envoi.statut, StatutDiffusionEnvoi.ECHEC)
        self.assertIn("indisponible", envoi.erreur)

    @patch("notifications.services.whatsapp_client")
    def test_respecte_la_taille_du_lot(self, mock_wa: MagicMock) -> None:
        diffusion = Diffusion.objects.create(message="Annonce")
        for i in range(5):
            DiffusionEnvoi.objects.create(diffusion=diffusion, abonne_id=f"a{i}", telephone=f"+23769900000{i}")
        mock_wa.send.return_value = None

        DiffusionService().traiter_lot_en_attente(2)

        self.assertEqual(mock_wa.send.call_count, 2)
        self.assertEqual(
            DiffusionEnvoi.objects.filter(diffusion=diffusion, statut=StatutDiffusionEnvoi.EN_ATTENTE).count(), 3
        )

    @patch("notifications.services.whatsapp_client")
    def test_termine_la_diffusion_quand_tout_est_resolu(self, mock_wa: MagicMock) -> None:
        diffusion = Diffusion.objects.create(message="Annonce")
        DiffusionEnvoi.objects.create(diffusion=diffusion, abonne_id="a1", telephone="+237699000001")
        mock_wa.send.return_value = None

        DiffusionService().traiter_lot_en_attente(10)

        diffusion.refresh_from_db()
        self.assertEqual(diffusion.statut, StatutDiffusion.TERMINEE)

    @patch("notifications.services.whatsapp_client")
    def test_ne_termine_pas_tant_qu_il_reste_des_lignes_en_attente(self, mock_wa: MagicMock) -> None:
        diffusion = Diffusion.objects.create(message="Annonce")
        for i in range(3):
            DiffusionEnvoi.objects.create(diffusion=diffusion, abonne_id=f"a{i}", telephone=f"+23769900000{i}")
        mock_wa.send.return_value = None

        DiffusionService().traiter_lot_en_attente(1)

        diffusion.refresh_from_db()
        self.assertEqual(diffusion.statut, StatutDiffusion.EN_COURS)
