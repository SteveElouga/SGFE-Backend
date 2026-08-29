"""Le message WhatsApp doit annoncer le même total que le PDF qu'il transporte.

Avant ces tests, le message affichait `facture.montant` — la consommation du
mois seule — pendant que sa pièce jointe additionnait la dette antérieure et
retranchait l'avoir. Deux chiffres différents dans le même envoi.

Ce n'est pas une imprécision d'affichage : **l'abonné paie ce qu'il lit dans
WhatsApp**, pas ce qu'il y a dans le PDF. Il payait donc le montant du mois, et
se faisait relancer pour une différence dont personne ne l'avait informé.
"""

from django.test import SimpleTestCase

from notifications.message_builder import build_message_facture


def _message(**surcharges) -> str:
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

    def test_affiche_un_seul_total(self):
        m = _message()
        self.assertIn("TOTAL À PAYER : 20 500 FCFA", m)

    def test_ne_detaille_pas_ce_qui_n_a_rien_a_detailler(self):
        m = _message()
        self.assertNotIn("Montant du mois", m)
        self.assertNotIn("Solde antérieur", m)
        self.assertNotIn("Avoir appliqué", m)
        self.assertNotIn("─", m)


class MessageFactureAvecAnterioriteTest(SimpleTestCase):
    def test_le_total_additionne_la_dette_anterieure(self):
        m = _message(solde_anterieur=3000, nb_factures_anterieures=1)
        self.assertIn("Montant du mois : 20 500 FCFA", m)
        self.assertIn("Solde antérieur (1 facture) : 3 000 FCFA", m)
        self.assertIn("TOTAL À PAYER : 23 500 FCFA", m)

    def test_accorde_le_pluriel_des_factures(self):
        m = _message(solde_anterieur=6000, nb_factures_anterieures=3)
        self.assertIn("Solde antérieur (3 factures) : 6 000 FCFA", m)

    def test_signale_l_anciennete_de_la_dette(self):
        # L'âge de la dette pèse plus que son montant : c'est lui qui fait payer.
        m = _message(solde_anterieur=3000, nb_factures_anterieures=1, plus_ancienne_echeance="15/06/2026")
        self.assertIn("dus depuis le 15/06/2026", m)

    def test_tait_l_anciennete_quand_l_echeance_est_inconnue(self):
        m = _message(solde_anterieur=3000, nb_factures_anterieures=1, plus_ancienne_echeance="")
        self.assertNotIn("dus depuis", m)


class MessageFactureAvecAvoirTest(SimpleTestCase):
    def test_l_avoir_est_retranche_du_total(self):
        m = _message(avoir_impute=500)
        self.assertIn("Avoir appliqué : − 500 FCFA", m)
        self.assertIn("TOTAL À PAYER : 20 000 FCFA", m)

    def test_un_avoir_superieur_a_la_facture_ne_cree_pas_de_total_negatif(self):
        # Un trop-perçu massif solde la facture ; il ne rend pas la régie
        # débitrice dans le corps du message.
        m = _message(montant=5000, avoir_impute=8000)
        self.assertIn("TOTAL À PAYER : 0 FCFA", m)
        self.assertNotIn("−5", m)

    def test_antériorite_et_avoir_se_combinent(self):
        m = _message(montant=20500, solde_anterieur=3000, nb_factures_anterieures=1, avoir_impute=500)
        self.assertIn("TOTAL À PAYER : 23 000 FCFA", m)


class MessageFactureLisibiliteTest(SimpleTestCase):
    def test_les_milliers_sont_separes(self):
        # Le PDF écrit « 23 500 ». Le message écrivait « 23500 » : sur cinq
        # chiffres, l'abonné doit relire son total sans le compter.
        m = _message(montant=1234567)
        self.assertIn("1 234 567", m)
        self.assertNotIn("1234567", m)

    def test_conserve_le_lien_et_la_piece_jointe(self):
        m = _message()
        self.assertIn("https://sgfe.example/espace/tok", m)
        self.assertIn("pièce jointe", m)
        self.assertIn("16/08/2026", m)

    def test_le_mobile_money_reste_optionnel(self):
        self.assertNotIn("Mobile Money", _message())
        self.assertIn("Mobile Money : +237600000000", _message(numero_mobile_money="+237600000000"))
