"""Tests du retry automatique des envois WhatsApp en échec.

Couvre EnvoiRepository.list_echecs_a_retenter (sélection du lot) et
EnvoiService.retenter_echecs (rejeu du dernier message, régénération du PDF,
plafond MAX_TENTATIVES_AUTO et abandon définitif). Les dépendances externes
(whatsapp_client, facturation_client, notifier_admins) sont mockées — voir
`test_services.py`/`test_recu_envoi.py` pour le même patron.
"""

import uuid
from datetime import timedelta
from unittest.mock import MagicMock, patch

from django.test import TestCase
from django.utils import timezone

from notifications.models import MAX_TENTATIVES_AUTO, Envoi, StatutEnvoi, TypeEnvoi
from notifications.repositories import EnvoiRepository
from notifications.services import EnvoiService
from notifications.whatsapp_client import WhatsAppDeliveryError


def _creer_echec(
    tentatives: int = 1,
    avec_pdf: bool = False,
    pdf_filename: str = "",
    dernier_message: str = "Message original",
    type_envoi: str = TypeEnvoi.FACTURE,
    paiement_id: str = "",
    statut: str = StatutEnvoi.ECHEC,
) -> Envoi:
    """Construit un Envoi en ECHEC (ou non) prêt pour le retry, avec son
    dernier message et son flag PDF déjà figés — comme le ferait
    `_tenter_envoi` lors de la tentative d'origine."""
    envoi = EnvoiRepository().create(
        facture_id=str(uuid.uuid4()),
        abonne_id=str(uuid.uuid4()),
        type_envoi=type_envoi,
        telephone="+237699000001",
        paiement_id=paiement_id,
    )
    envoi.statut = statut
    envoi.tentatives = tentatives
    envoi.avec_pdf = avec_pdf
    envoi.pdf_filename = pdf_filename
    envoi.dernier_message = dernier_message
    envoi.save()
    return envoi


class TestListEchecsARetenter(TestCase):
    """Tests de EnvoiRepository.list_echecs_a_retenter."""

    def test_selectionne_seulement_les_echecs_sous_le_plafond(self) -> None:
        sous_plafond = _creer_echec(tentatives=MAX_TENTATIVES_AUTO - 1)
        _creer_echec(tentatives=MAX_TENTATIVES_AUTO)  # au plafond — exclu
        _creer_echec(statut=StatutEnvoi.ENVOYE)  # pas un échec — exclu
        _creer_echec(statut=StatutEnvoi.EN_ATTENTE)  # pas un échec — exclu

        lot = EnvoiRepository().list_echecs_a_retenter(20)

        self.assertEqual([e.id for e in lot], [sous_plafond.id])

    def test_ordre_du_plus_ancien_au_plus_recent(self) -> None:
        premier = _creer_echec(dernier_message="m1")
        second = _creer_echec(dernier_message="m2")
        # Force un ordre de création déterministe : `created_at` est
        # `auto_now_add`, donc non réglable à la création — on le corrige
        # après coup pour ne pas dépendre de la résolution de l'horloge.
        maintenant = timezone.now()
        Envoi.objects.filter(id=premier.id).update(created_at=maintenant - timedelta(hours=2))
        Envoi.objects.filter(id=second.id).update(created_at=maintenant - timedelta(hours=1))

        lot = EnvoiRepository().list_echecs_a_retenter(20)

        self.assertEqual([e.id for e in lot], [premier.id, second.id])

    def test_respecte_la_taille_du_lot(self) -> None:
        for i in range(5):
            _creer_echec(dernier_message=f"m{i}")

        lot = EnvoiRepository().list_echecs_a_retenter(2)

        self.assertEqual(len(lot), 2)


class TestRetenterEchecs(TestCase):
    """Tests de EnvoiService.retenter_echecs."""

    @patch("notifications.services.whatsapp_client")
    def test_retente_avec_le_meme_message_sans_le_recalculer(self, mock_wa: MagicMock) -> None:
        """Le message rejoué doit être EXACTEMENT `dernier_message` — jamais
        recalculé (pas d'appel à facturation_client/abonne_client)."""
        envoi = _creer_echec(tentatives=2, dernier_message="Bonjour, voici votre facture de 7500 FCFA.")
        mock_wa.send.return_value = None

        lot = EnvoiService().retenter_echecs(20)

        self.assertEqual(len(lot), 1)
        envoi.refresh_from_db()
        self.assertEqual(envoi.statut, StatutEnvoi.ENVOYE)
        self.assertEqual(envoi.tentatives, 3)
        mock_wa.send.assert_called_once_with(envoi.telephone, "Bonjour, voici votre facture de 7500 FCFA.")

    @patch("notifications.services.whatsapp_client")
    def test_ignore_les_envois_au_dela_du_plafond(self, mock_wa: MagicMock) -> None:
        envoi = _creer_echec(tentatives=MAX_TENTATIVES_AUTO)

        lot = EnvoiService().retenter_echecs(20)

        self.assertEqual(lot, [])
        mock_wa.send.assert_not_called()
        envoi.refresh_from_db()
        self.assertEqual(envoi.tentatives, MAX_TENTATIVES_AUTO)  # inchangé

    @patch("notifications.services.facturation_client")
    @patch("notifications.services.whatsapp_client")
    def test_regenere_le_pdf_facture_si_avec_pdf(self, mock_wa: MagicMock, mock_fact: MagicMock) -> None:
        envoi = _creer_echec(avec_pdf=True, pdf_filename="facture.pdf", type_envoi=TypeEnvoi.FACTURE)
        mock_fact.get_facture_pdf.return_value = (b"%PDF-1", "facture.pdf")
        mock_wa.send_with_pdf.return_value = None

        EnvoiService().retenter_echecs(20)

        mock_fact.get_facture_pdf.assert_called_once_with(envoi.facture_id)
        mock_fact.generer_recu_paiement_pdf.assert_not_called()
        mock_wa.send_with_pdf.assert_called_once()
        envoi.refresh_from_db()
        self.assertEqual(envoi.statut, StatutEnvoi.ENVOYE)

    @patch("notifications.services.facturation_client")
    @patch("notifications.services.whatsapp_client")
    def test_regenere_le_pdf_recu_via_le_paiement_id(self, mock_wa: MagicMock, mock_fact: MagicMock) -> None:
        pid = str(uuid.uuid4())
        envoi = _creer_echec(avec_pdf=True, pdf_filename="recu.pdf", type_envoi=TypeEnvoi.RECU, paiement_id=pid)
        mock_fact.generer_recu_paiement_pdf.return_value = (b"%PDF-recu", "recu.pdf")
        mock_wa.send_with_pdf.return_value = None

        EnvoiService().retenter_echecs(20)

        mock_fact.generer_recu_paiement_pdf.assert_called_once_with(pid, envoi.facture_id)
        mock_fact.get_facture_pdf.assert_not_called()

    @patch("notifications.services.facturation_client")
    @patch("notifications.services.whatsapp_client")
    def test_pas_de_regeneration_pdf_si_avec_pdf_faux(self, mock_wa: MagicMock, mock_fact: MagicMock) -> None:
        _creer_echec(avec_pdf=False)
        mock_wa.send.return_value = None

        EnvoiService().retenter_echecs(20)

        mock_fact.get_facture_pdf.assert_not_called()
        mock_fact.generer_recu_paiement_pdf.assert_not_called()
        mock_wa.send.assert_called_once()
        mock_wa.send_with_pdf.assert_not_called()

    @patch("notifications.services.notifier_admins")
    @patch("notifications.services.whatsapp_client")
    def test_echec_persistant_sous_le_plafond_ne_notifie_pas_l_abandon(
        self, mock_wa: MagicMock, mock_notify: MagicMock
    ) -> None:
        """Un retry qui échoue encore, mais reste sous le plafond, ne doit
        déclencher QUE la notification d'échec habituelle (`_tenter_envoi`) —
        pas d'abandon définitif prématuré."""
        _creer_echec(tentatives=2)
        mock_wa.send.side_effect = WhatsAppDeliveryError("toujours en panne")

        EnvoiService().retenter_echecs(20)

        evenements = [c.kwargs.get("evenement") for c in mock_notify.call_args_list]
        self.assertEqual(evenements.count("ECHEC_WHATSAPP"), 1)
        self.assertNotIn("ABANDON_RETRY_WHATSAPP", evenements)

    @patch("notifications.services.notifier_admins")
    @patch("notifications.services.whatsapp_client")
    def test_franchissement_du_plafond_notifie_l_abandon_definitif(
        self, mock_wa: MagicMock, mock_notify: MagicMock
    ) -> None:
        """La tentative qui fait passer `tentatives` à MAX_TENTATIVES_AUTO,
        si elle échoue encore, doit loguer/notifier un abandon DISTINCT —
        pas un doublon du message d'échec normal."""
        envoi = _creer_echec(tentatives=MAX_TENTATIVES_AUTO - 1)
        mock_wa.send.side_effect = WhatsAppDeliveryError("toujours en panne")

        EnvoiService().retenter_echecs(20)

        envoi.refresh_from_db()
        self.assertEqual(envoi.tentatives, MAX_TENTATIVES_AUTO)
        self.assertEqual(envoi.statut, StatutEnvoi.ECHEC)
        evenements = [c.kwargs.get("evenement") for c in mock_notify.call_args_list]
        self.assertEqual(evenements.count("ECHEC_WHATSAPP"), 1)
        self.assertEqual(evenements.count("ABANDON_RETRY_WHATSAPP"), 1)

    @patch("notifications.services.whatsapp_client")
    def test_franchissement_du_plafond_bloque_tout_retry_ulterieur(self, mock_wa: MagicMock) -> None:
        """Cap dur : une fois le plafond atteint, l'envoi ne doit plus jamais
        être sélectionné par un passage ultérieur du job."""
        envoi = _creer_echec(tentatives=MAX_TENTATIVES_AUTO - 1)
        mock_wa.send.side_effect = WhatsAppDeliveryError("en panne")

        EnvoiService().retenter_echecs(20)  # atteint le plafond, échoue encore
        lot_suivant = EnvoiService().retenter_echecs(20)  # passage ultérieur du job

        self.assertEqual(lot_suivant, [])
        envoi.refresh_from_db()
        self.assertEqual(envoi.tentatives, MAX_TENTATIVES_AUTO)

    @patch("notifications.services.whatsapp_client")
    def test_reussite_apres_retry_repasse_a_envoye(self, mock_wa: MagicMock) -> None:
        envoi = _creer_echec(tentatives=1)
        mock_wa.send.return_value = None

        EnvoiService().retenter_echecs(20)

        envoi.refresh_from_db()
        self.assertEqual(envoi.statut, StatutEnvoi.ENVOYE)
        self.assertIsNotNone(envoi.date_envoi)

    @patch("notifications.services.whatsapp_client")
    def test_traite_plusieurs_echecs_du_lot(self, mock_wa: MagicMock) -> None:
        e1 = _creer_echec(dernier_message="m1")
        e2 = _creer_echec(dernier_message="m2")
        mock_wa.send.return_value = None

        lot = EnvoiService().retenter_echecs(20)

        self.assertEqual({e.id for e in lot}, {e1.id, e2.id})
        self.assertEqual(mock_wa.send.call_count, 2)
