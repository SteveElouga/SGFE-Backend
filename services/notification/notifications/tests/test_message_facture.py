"""Le message WhatsApp doit annoncer le même total que le PDF qu'il transporte.

Avant ces tests, le message affichait `facture.montant` — la consommation du
mois seule — pendant que sa pièce jointe additionnait la dette antérieure et
retranchait l'avoir. Deux chiffres différents dans le même envoi.

Ce n'est pas une imprécision d'affichage : **l'abonné paie ce qu'il lit dans
WhatsApp**, pas ce qu'il y a dans le PDF. Il payait donc le montant du mois, et
se faisait relancer pour une différence dont personne ne l'avait informé.
"""

from typing import Any

from django.test import SimpleTestCase

from notifications.message_builder import build_message_facture


def _message(**surcharges: Any) -> str:
    base = {
        "prenom_nom": "Jean DUPONT",
        "periode": "Juillet 2026",
        "consommation": 41,
        "montant": 20500,
        "date_limite": "27/07/2026",
        "token": "tok",
        "date_expiration_token": "16/08/2026",
        "frontend_url": "https://sgfe.example",
    }
    return build_message_facture(**{**base, **surcharges})


class MessageFactureOrdinaireTest(SimpleTestCase):
    """Sans antériorité ni avoir, le message garde sa forme courte."""

    def test_affiche_un_seul_total(self) -> None:
        m = _message()
        self.assertIn("TOTAL À PAYER : 20 500 FCFA", m)

    def test_ne_detaille_pas_ce_qui_n_a_rien_a_detailler(self) -> None:
        m = _message()
        self.assertNotIn("Montant du mois", m)
        self.assertNotIn("Solde antérieur", m)
        self.assertNotIn("Avoir appliqué", m)
        self.assertNotIn("─", m)


class MessageFactureAvecAnterioriteTest(SimpleTestCase):
    def test_le_total_additionne_la_dette_anterieure(self) -> None:
        m = _message(solde_anterieur=3000, nb_factures_anterieures=1)
        self.assertIn("Montant du mois : 20 500 FCFA", m)
        self.assertIn("Solde antérieur (1 facture) : 3 000 FCFA", m)
        self.assertIn("TOTAL À PAYER : 23 500 FCFA", m)

    def test_accorde_le_pluriel_des_factures(self) -> None:
        m = _message(solde_anterieur=6000, nb_factures_anterieures=3)
        self.assertIn("Solde antérieur (3 factures) : 6 000 FCFA", m)

    def test_signale_l_anciennete_de_la_dette(self) -> None:
        # L'âge de la dette pèse plus que son montant : c'est lui qui fait payer.
        m = _message(solde_anterieur=3000, nb_factures_anterieures=1, plus_ancienne_echeance="15/06/2026")
        self.assertIn("dus depuis le 15/06/2026", m)

    def test_tait_l_anciennete_quand_l_echeance_est_inconnue(self) -> None:
        m = _message(solde_anterieur=3000, nb_factures_anterieures=1, plus_ancienne_echeance="")
        self.assertNotIn("dus depuis", m)


class MessageFactureAvecAvoirTest(SimpleTestCase):
    def test_l_avoir_est_retranche_du_total(self) -> None:
        m = _message(avoir_impute=500)
        self.assertIn("Avoir appliqué : − 500 FCFA", m)
        self.assertIn("TOTAL À PAYER : 20 000 FCFA", m)

    def test_un_avoir_superieur_a_la_facture_ne_cree_pas_de_total_negatif(self) -> None:
        # Un trop-perçu massif solde la facture ; il ne rend pas la régie
        # débitrice dans le corps du message.
        m = _message(montant=5000, avoir_impute=8000)
        self.assertIn("TOTAL À PAYER : 0 FCFA", m)
        self.assertNotIn("−5", m)

    def test_antériorite_et_avoir_se_combinent(self) -> None:
        m = _message(montant=20500, solde_anterieur=3000, nb_factures_anterieures=1, avoir_impute=500)
        self.assertIn("TOTAL À PAYER : 23 000 FCFA", m)


class MessageFactureLisibiliteTest(SimpleTestCase):
    def test_les_milliers_sont_separes(self) -> None:
        # Le PDF écrit « 23 500 ». Le message écrivait « 23500 » : sur cinq
        # chiffres, l'abonné doit relire son total sans le compter.
        m = _message(montant=1234567)
        self.assertIn("1 234 567", m)
        self.assertNotIn("1234567", m)

    def test_conserve_le_lien_et_la_piece_jointe(self) -> None:
        m = _message()
        self.assertIn("https://sgfe.example/espace/tok", m)
        self.assertIn("pièce jointe", m)
        self.assertIn("16/08/2026", m)

    def test_le_mobile_money_reste_optionnel(self) -> None:
        self.assertNotIn("Mobile Money", _message())
        self.assertIn("Mobile Money : +237600000000", _message(numero_mobile_money="+237600000000"))


class MessageAnnulationPaiementTest(SimpleTestCase):
    """Un versement annulé laisse l'abonné avec un reçu qui ne vaut plus rien.

    Se taire, c'est le laisser découvrir la chose à la relance suivante — et
    croire à une erreur de la régie alors qu'on vient précisément d'en corriger
    une.
    """

    def _msg(self, **surcharges: Any) -> str:
        from notifications.message_builder import build_message_annulation_paiement

        base = {
            "prenom_nom": "Jean DUPONT",
            "periode": "Août 2026",
            "solde_restant": 13500,
            "lien_espace": "https://x/espace/tok",
        }
        return build_message_annulation_paiement(**{**base, **surcharges})

    def test_annonce_ce_qui_reste_du(self) -> None:
        # C'est le seul chiffre qui appelle une action.
        #
        # On compose l'attendu avec `_fcfa` plutôt que de recopier le séparateur
        # de milliers : c'est une espace fine insécable (U+202F), et l'écrire à
        # la main dans un test le rend faux pour une raison invisible à l'œil.
        from notifications.message_builder import _fcfa

        self.assertIn(f"Reste à payer : {_fcfa(13500)} FCFA", self._msg())

    def test_nomme_la_periode_concernee(self) -> None:
        self.assertIn("Août 2026", self._msg())

    def test_ne_nomme_pas_le_motif(self) -> None:
        # Le motif est écrit pour la piste d'audit, dans le vocabulaire du
        # guichet (« doublon », « erreur de saisie »). Le servir à l'abonné
        # l'inquiéterait sans l'informer.
        m = self._msg()
        for mot in ("doublon", "erreur de saisie", "motif"):
            self.assertNotIn(mot, m.lower())

    def test_invite_a_signaler_un_versement_reellement_effectue(self) -> None:
        # Si l'annulation est elle-même une erreur, l'abonné est le seul à
        # pouvoir le dire.
        self.assertIn("contactez-nous", self._msg())

    def test_les_milliers_sont_separes(self) -> None:
        from notifications.message_builder import _fcfa

        self.assertIn(_fcfa(1234567), self._msg(solde_restant=1234567))
        self.assertNotIn("1234567", self._msg(solde_restant=1234567))


class MessageAnnulationFactureTest(SimpleTestCase):
    """Une facture annulée avant tout paiement n'a pas de versement à annuler —
    le dire comme tel serait faux pour quelqu'un qui n'a jamais payé."""

    def _msg(self, **surcharges: Any) -> str:
        from notifications.message_builder import build_message_annulation_facture

        base = {"prenom_nom": "Jean DUPONT", "periode": "Août 2026", "lien_espace": "https://x/espace/tok"}
        return build_message_annulation_facture(**{**base, **surcharges})

    def test_ne_parle_pas_de_versement(self) -> None:
        m = self._msg().lower()
        for mot in ("versement", "reçu"):
            self.assertNotIn(mot, m)

    def test_dit_qu_il_n_y_a_rien_a_payer(self) -> None:
        self.assertIn("rien à payer", self._msg())

    def test_nomme_la_periode_concernee(self) -> None:
        self.assertIn("Août 2026", self._msg())
