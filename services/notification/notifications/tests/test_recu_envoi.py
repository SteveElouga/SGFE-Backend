"""Tests de l'envoi automatique du reçu de paiement.

Couvre build_message_recu (message pur) et EnvoiService.envoyer_recu
(orchestration : reçu PDF via Facturation + envoi WhatsApp). Les dépendances
externes (grpc_clients, whatsapp_client) sont mockées.
"""

import uuid
from unittest.mock import MagicMock, patch

from django.test import TestCase

from notifications.message_builder import build_message_recu
from notifications.models import StatutEnvoi, TypeEnvoi
from notifications.services import EnvoiService


def _facture_mock(facture_id: str, abonne_id: str, date_releve: str = "2026-06-26") -> MagicMock:
    mock = MagicMock()
    mock.facture_id = facture_id
    mock.abonne_id = abonne_id
    mock.date_releve = date_releve
    mock.date_limite_paiement = "2026-07-01"
    return mock


def _abonne_mock(abonne_id: str, nom: str = "KONE", prenom: str = "Mariam") -> MagicMock:
    mock = MagicMock()
    mock.abonne_id = abonne_id
    mock.nom = nom
    mock.prenom = prenom
    mock.telephone_whatsapp = "+237699000002"
    return mock


class TestBuildMessageRecu(TestCase):
    def test_versement_partiel_mentionne_le_solde(self):
        msg = build_message_recu("Jean DUPONT", "Juin 2026", 10750.0, 10750.0)
        self.assertIn("10750 FCFA", msg)
        self.assertIn("Solde restant dû : 10750 FCFA", msg)
        self.assertIn("pièce jointe", msg)

    def test_facture_soldee_ne_mentionne_pas_de_solde(self):
        msg = build_message_recu("Jean DUPONT", "Juin 2026", 21500.0, 0.0)
        self.assertIn("soldée", msg)
        self.assertNotIn("Solde restant", msg)


class TestEnvoiServiceEnvoyerRecu(TestCase):
    @patch("notifications.services.whatsapp_client")
    @patch("notifications.services.abonne_client")
    @patch("notifications.services.facturation_client")
    def test_succes_envoie_le_recu_en_piece_jointe(self, mock_fact, mock_abonne, mock_wa):
        fid, aid, pid = str(uuid.uuid4()), str(uuid.uuid4()), str(uuid.uuid4())
        mock_fact.get_facture.return_value = _facture_mock(fid, aid)
        mock_fact.generer_recu_paiement_pdf.return_value = (b"%PDF recu", "REC-2026-06-0002-1.pdf")
        mock_abonne.get_abonne.return_value = _abonne_mock(aid)

        envoi = EnvoiService().envoyer_recu(pid, fid, aid, 10750.0, 10750.0)

        self.assertEqual(envoi.statut, StatutEnvoi.ENVOYE)
        self.assertEqual(envoi.type_envoi, TypeEnvoi.RECU)
        mock_wa.send_with_pdf.assert_called_once()
        # Le PDF du reçu (et son nom de fichier) est bien transmis à WhatsApp.
        args = mock_wa.send_with_pdf.call_args.args
        self.assertEqual(args[2], b"%PDF recu")
        self.assertEqual(args[3], "REC-2026-06-0002-1.pdf")

    @patch("notifications.services.whatsapp_client")
    @patch("notifications.services.abonne_client")
    @patch("notifications.services.facturation_client")
    def test_pdf_indisponible_envoie_le_message_seul(self, mock_fact, mock_abonne, mock_wa):
        fid, aid, pid = str(uuid.uuid4()), str(uuid.uuid4()), str(uuid.uuid4())
        mock_fact.get_facture.return_value = _facture_mock(fid, aid)
        mock_fact.generer_recu_paiement_pdf.return_value = (b"", "")  # Facturation KO
        mock_abonne.get_abonne.return_value = _abonne_mock(aid)

        envoi = EnvoiService().envoyer_recu(pid, fid, aid, 10750.0, 0.0)

        self.assertEqual(envoi.statut, StatutEnvoi.ENVOYE)
        mock_wa.send.assert_called_once()  # message texte seul
        mock_wa.send_with_pdf.assert_not_called()

    @patch("notifications.services.notifier_admins")
    @patch("notifications.services.whatsapp_client")
    @patch("notifications.services.abonne_client")
    @patch("notifications.services.facturation_client")
    def test_amont_ko_degrade_en_echec(self, mock_fact, mock_abonne, mock_wa, mock_notify):
        import grpc

        class _RpcError(grpc.RpcError):
            def details(self) -> str:
                return "UNAVAILABLE: facturation-service"

        fid, aid, pid = str(uuid.uuid4()), str(uuid.uuid4()), str(uuid.uuid4())
        mock_fact.get_facture.side_effect = _RpcError()

        envoi = EnvoiService().envoyer_recu(pid, fid, aid, 10750.0, 10750.0)  # ne doit pas lever

        self.assertEqual(envoi.statut, StatutEnvoi.ECHEC)
        self.assertEqual(envoi.type_envoi, TypeEnvoi.RECU)
        mock_wa.send.assert_not_called()
