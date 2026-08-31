"""L'escalade des impayés dit la vérité, et n'envoie qu'un message à la fois.

Trois défauts se tenaient ensemble dans ce cron :

1. Les trois rappels étaient tentés dans le MÊME passage, puis la suspension.
   Une facture très en retard recevait quatre messages en quelques secondes,
   dont trois annonçaient un retard faux.
2. L'étape était marquée envoyée sans savoir si le message était parti —
   `envoyer_relance` avalait tout. Un abonné pouvait être coupé sans avoir été
   prévenu, et l'escalade continuait comme si tout lui avait été dit.
3. Le délai avant suspension était écrit en dur dans le gabarit du message
   (« dans 3 jours »), alors que ce cron le lit dans Config.
"""

from datetime import date, timedelta
from decimal import Decimal
from unittest.mock import MagicMock, patch

from django.test import TestCase

from paiements.models import SoldeFacture, SuiviImpaye
from paiements.repositories import SoldeFactureRepository
from paiements.services import ImpayeService

ABONNE = "abonne-relance"

_DELAIS = {
    "rappel_1": 0,
    "rappel_2": 3,
    "avertissement": 7,
    "suspension": 10,
    "suspension_auto": True,
    "suspension_relances": 5,
}


def _solde(facture_id: str, jours_retard: int) -> SoldeFacture:
    return SoldeFactureRepository().create(
        facture_id=facture_id,
        abonne_id=ABONNE,
        montant_total=Decimal("10000"),
        date_limite_paiement=date.today() - timedelta(days=jours_retard),
        campagne_id="camp-1",
    )


@patch("paiements.services.NotificationServiceClient")
@patch("paiements.services.AbonneServiceClient")
@patch("paiements.services.ConfigServiceClient")
class TestUnSeulMessageParPassage(TestCase):
    def _preparer(self, mock_config, mock_notif, mock_abonne, envoi_reussi: bool = True):
        mock_config.return_value.get_delais_impayes.return_value = dict(_DELAIS)
        notif = MagicMock()
        notif.envoyer_relance.return_value = envoi_reussi
        mock_notif.return_value = notif
        mock_abonne.return_value = MagicMock()
        return notif, mock_abonne.return_value

    def test_facture_tres_en_retard_ne_recoit_qu_un_message(self, mock_config, mock_abonne, mock_notif) -> None:
        """31 jours de retard : l'avis de suspension, et lui seul.

        Le premier passage envoyait quatre messages : « échéance aujourd'hui »,
        « depuis 3 jours », « depuis 7 jours, suspendue dans 3 jours », puis
        « votre ligne a été suspendue ». Trois d'entre eux étaient faux, et le
        dernier rendait les trois autres absurdes.

        Le cas n'est pas théorique : une régularisation saisie avec sa vraie
        échéance est immédiatement à plusieurs mois de retard.
        """
        notif, abonne = self._preparer(mock_config, mock_notif, mock_abonne)
        _solde("vieille", jours_retard=31)

        ImpayeService().verifier_et_escalader()

        self.assertEqual(notif.envoyer_relance.call_count, 1)
        self.assertEqual(notif.envoyer_relance.call_args.kwargs["etape"], 4)
        abonne.suspendre_abonne.assert_called_once_with(ABONNE)

    def test_les_etapes_sautees_ne_sont_pas_marquees_envoyees(self, mock_config, mock_abonne, mock_notif) -> None:
        """Elles ne l'ont pas été. La piste d'audit doit le dire.

        Les marquer « envoyées » pour faire propre effacerait la seule trace de
        ce que l'abonné a réellement reçu — et personne ne pourrait plus
        répondre à « m'a-t-on prévenu ? ».
        """
        self._preparer(mock_config, mock_notif, mock_abonne)
        _solde("vieille", jours_retard=31)

        ImpayeService().verifier_et_escalader()

        suivi = SuiviImpaye.objects.get(facture_id="vieille")
        self.assertFalse(suivi.rappel_1_envoye)
        self.assertFalse(suivi.rappel_2_envoye)
        self.assertFalse(suivi.avertissement_envoye)
        self.assertTrue(suivi.suspension_effectuee)
        self.assertEqual(suivi.etape_actuelle, 4)

    def test_une_facture_qui_vieillit_normalement_suit_bien_les_etapes(
        self, mock_config, mock_abonne, mock_notif
    ) -> None:
        """Le comportement du cas normal est inchangé — c'est l'essentiel.

        À chaque passage, l'étape la plus avancée que le retard justifie est
        aussi la suivante : étape 1 dès le premier jour de retard, étape 2 à
        J+3, étape 3 à J+7.

        Le premier jour testé est J+1 et non J+0 : `list_impayes` filtre sur
        `date_limite_paiement < aujourd'hui`, donc une facture échue le jour même
        n'est pas encore vue comme impayée — quel que soit le délai configuré
        pour le rappel 1.
        """
        notif, _ = self._preparer(mock_config, mock_notif, mock_abonne)

        for jours, etape_attendue in ((1, 1), (3, 2), (7, 3)):
            notif.envoyer_relance.reset_mock()
            SoldeFacture.objects.all().delete()
            SuiviImpaye.objects.all().delete()
            _solde(f"f-{jours}", jours_retard=jours)

            ImpayeService().verifier_et_escalader()

            self.assertEqual(notif.envoyer_relance.call_count, 1, f"à J+{jours}")
            self.assertEqual(notif.envoyer_relance.call_args.kwargs["etape"], etape_attendue, f"à J+{jours}")

    def test_le_delai_avant_suspension_transmis_est_le_vrai(self, mock_config, mock_abonne, mock_notif) -> None:
        """Le gabarit écrivait « suspendue dans 3 jours » en dur.

        À J+8 avec une suspension à J+10, il reste **2** jours. Le cron a la
        valeur configurée sous la main : il la transmet, plutôt que le service de
        notification aille relire une source de vérité qui n'est pas la sienne.
        """
        notif, _ = self._preparer(mock_config, mock_notif, mock_abonne)
        _solde("f-j8", jours_retard=8)

        ImpayeService().verifier_et_escalader()

        kwargs = notif.envoyer_relance.call_args.kwargs
        self.assertEqual(kwargs["etape"], 3)
        self.assertEqual(kwargs["jours_avant_suspension"], 2)

    def test_le_delai_suit_la_configuration(self, mock_config, mock_abonne, mock_notif) -> None:
        """Suspension réglée à 20 jours : à J+8, il reste 12 jours, pas 3."""
        notif, _ = self._preparer(mock_config, mock_notif, mock_abonne)
        mock_config.return_value.get_delais_impayes.return_value = {**_DELAIS, "suspension": 20}
        _solde("f-j8", jours_retard=8)

        ImpayeService().verifier_et_escalader()

        self.assertEqual(notif.envoyer_relance.call_args.kwargs["jours_avant_suspension"], 12)


@patch("paiements.services.NotificationServiceClient")
@patch("paiements.services.AbonneServiceClient")
@patch("paiements.services.ConfigServiceClient")
class TestRelanceNonPartie(TestCase):
    """Une relance qui n'est pas partie n'est pas une relance."""

    def _preparer(self, mock_config, mock_notif, mock_abonne, envoi_reussi: bool):
        mock_config.return_value.get_delais_impayes.return_value = dict(_DELAIS)
        notif = MagicMock()
        notif.envoyer_relance.return_value = envoi_reussi
        mock_notif.return_value = notif
        mock_abonne.return_value = MagicMock()
        return notif, mock_abonne.return_value

    def test_l_etape_reste_a_retenter_si_le_message_n_est_pas_parti(self, mock_config, mock_abonne, mock_notif) -> None:
        """Le drapeau était posé inconditionnellement.

        `envoyer_relance` n'échoue jamais : erreurs gRPC et échecs WhatsApp sont
        avalés. Un abonné pouvait donc être « relancé » quatre fois sans rien
        recevoir, puis coupé — l'escalade continuant comme si tout lui avait été
        dit, et sans jamais retenter.
        """
        notif, _ = self._preparer(mock_config, mock_notif, mock_abonne, envoi_reussi=False)
        _solde("f-echec", jours_retard=1)

        ImpayeService().verifier_et_escalader()

        notif.envoyer_relance.assert_called_once()
        suivi = SuiviImpaye.objects.get(facture_id="f-echec")
        self.assertFalse(suivi.rappel_1_envoye)
        self.assertIsNone(suivi.date_rappel_1)

        # Le passage du lendemain retente la même étape.
        notif.envoyer_relance.reset_mock()
        notif.envoyer_relance.return_value = True
        ImpayeService().verifier_et_escalader()

        notif.envoyer_relance.assert_called_once()
        self.assertEqual(notif.envoyer_relance.call_args.kwargs["etape"], 1)
        suivi.refresh_from_db()
        self.assertTrue(suivi.rappel_1_envoye)

    def test_la_suspension_a_lieu_meme_si_le_message_echoue_mais_les_admins_le_savent(
        self, mock_config, mock_abonne, mock_notif
    ) -> None:
        """La coupure est la décision ; le message n'en est que l'annonce.

        Différer la coupure parce que WhatsApp est en panne serait perdre de la
        recette. Mais quelqu'un a été coupé sans l'apprendre : il faut que ça se
        sache avant qu'il appelle.
        """
        notif, abonne = self._preparer(mock_config, mock_notif, mock_abonne, envoi_reussi=False)
        _solde("f-susp", jours_retard=12)

        ImpayeService().verifier_et_escalader()

        abonne.suspendre_abonne.assert_called_once_with(ABONNE)
        self.assertTrue(SuiviImpaye.objects.get(facture_id="f-susp").suspension_effectuee)
        detail = notif.notifier_admins.call_args.kwargs["detail"]
        self.assertIn("N'A PAS PU ÊTRE PRÉVENU", detail)
