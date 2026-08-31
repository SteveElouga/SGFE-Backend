"""Les relances annoncent le reste dû et le retard réels.

Chaîne du défaut, telle qu'elle était :

1. le cron itérait des `SoldeFacture` — il avait donc `solde_restant` en main ;
2. il appelait `envoyer_relance(facture_id, abonne_id, etape)` — le proto ne
   transportait aucun montant ;
3. ce service relisait la facture via `GetFacture` — et `FactureResponse`
   n'expose ni `montant_paye` ni `solde_restant` ;
4. il n'avait donc que `facture.montant`, et c'est ce qu'il annonçait.

Le montant était bien relu à l'envoi, mais **relu à la mauvaise source**. Il
n'était pas périmé : il était structurellement faux dès qu'un versement partiel
existait. Or les factures PARTIELLE sont bien relancées — la pause après acompte
ne dure que quelques jours.

Le client Paiement était pourtant déjà là, utilisé pour la seule étape 5.
"""

import uuid
from datetime import date, timedelta
from unittest.mock import MagicMock, patch

from django.test import TestCase

from notifications.message_builder import (
    build_message_relance_1,
    build_message_relance_2,
    build_message_relance_3,
    build_message_relance_4,
)
from notifications.models import StatutEnvoi, TypeEnvoi
from notifications.services import EnvoiService, _jours_de_retard


def _facture(facture_id: str, abonne_id: str, jours_retard: int, montant: float = 10000.0) -> MagicMock:
    m = MagicMock()
    m.facture_id = facture_id
    m.abonne_id = abonne_id
    m.montant = montant
    m.date_releve = "2026-07-01"
    m.date_limite_paiement = (date.today() - timedelta(days=jours_retard)).isoformat()
    return m


def _abonne(abonne_id: str) -> MagicMock:
    m = MagicMock()
    m.abonne_id = abonne_id
    m.nom = "DUPONT"
    m.prenom = "Jean"
    m.telephone_whatsapp = "+237699000001"
    return m


class TestJoursDeRetard(TestCase):
    """Le retard se calcule, il ne se suppose pas."""

    def test_echeance_du_jour(self):
        self.assertEqual(_jours_de_retard(date.today().isoformat()), 0)

    def test_retard_reel(self):
        self.assertEqual(_jours_de_retard((date.today() - timedelta(days=31)).isoformat()), 31)

    def test_echeance_future_ne_donne_pas_de_retard_negatif(self):
        self.assertEqual(_jours_de_retard((date.today() + timedelta(days=5)).isoformat()), 0)

    def test_date_illisible_ou_vide(self):
        self.assertEqual(_jours_de_retard(""), 0)
        self.assertEqual(_jours_de_retard("pas-une-date"), 0)

    def test_horodatage_complet(self):
        jour = (date.today() - timedelta(days=3)).isoformat()
        self.assertEqual(_jours_de_retard(f"{jour}T08:00:00+00:00"), 3)


class TestGabaritsVeridiques(TestCase):
    """Les quatre gabarits, sans aucun délai ni montant écrit en dur."""

    def test_relance_2_annonce_le_vrai_retard(self):
        msg = build_message_relance_2("Jean DUPONT", "Juillet", 2000.0, jours_retard=31)
        self.assertIn("depuis 31 jours", msg)
        self.assertNotIn("depuis 3 jours", msg)

    def test_relance_2_accorde_le_singulier(self):
        self.assertIn("depuis 1 jour.", build_message_relance_2("J D", "Juillet", 2000.0, jours_retard=1))

    def test_relance_3_annonce_le_delai_transmis(self):
        msg = build_message_relance_3("Jean DUPONT", 2000.0, jours_retard=8, jours_avant_suspension=2)
        self.assertIn("depuis 8 jours", msg)
        self.assertIn("dans les 2 jours", msg)
        self.assertNotIn("dans les 3 jours", msg)

    def test_relance_3_sans_delai_connu_n_annonce_pas_de_delai_faux(self):
        msg = build_message_relance_3("Jean DUPONT", 2000.0, jours_retard=8, jours_avant_suspension=0)
        self.assertIn("sera suspendue", msg)
        self.assertNotIn("dans les", msg)

    def test_montant_illisible_n_est_pas_imprime(self):
        """Un chiffre inconnu ne s'imprime pas — même règle que la ligne
        d'antériorité du message de facture, omise plutôt que faussée."""
        for msg in (
            build_message_relance_1("J D", "Juillet", None, "https://x", 3),
            build_message_relance_2("J D", "Juillet", None, 3),
            build_message_relance_3("J D", None, 3, 2),
        ):
            self.assertNotIn("FCFA", msg)
            self.assertNotIn("None", msg)

    def test_suspension_dit_quoi_payer_pour_etre_retabli(self):
        """Elle renvoyait vers un numéro de téléphone, sans montant à régler.

        Et le montant cité était celui d'UNE facture, alors que le
        rétablissement exige l'extinction de la dette totale (RS-005) : régler
        cette somme ne rétablissait même pas la ligne.
        """
        msg = build_message_relance_4("Jean DUPONT", 27500.0, "Juillet", "+237 690")
        self.assertIn("Pour être rétabli", msg)
        self.assertIn("27500 FCFA", msg)
        self.assertIn("+237 690", msg)

    def test_suspension_sans_dette_lisible_renvoie_au_service(self):
        msg = build_message_relance_4("Jean DUPONT", None, "Juillet", "+237 690")
        self.assertNotIn("FCFA", msg)
        self.assertIn("+237 690", msg)

    def test_suspension_sans_telephone_ne_laisse_pas_la_phrase_en_suspens(self):
        """« contactez notre service au  » — Config injoignable tronquait la phrase."""
        msg = build_message_relance_4("Jean DUPONT", 27500.0, "Juillet", "")
        self.assertNotIn("au .", msg)
        self.assertIn("Contactez notre service", msg)


@patch("notifications.services.whatsapp_client")
@patch("notifications.services.paiement_client")
@patch("notifications.services.abonne_client")
@patch("notifications.services.facturation_client")
class TestEnvoiLitLaBonneSource(TestCase):
    """Le montant annoncé vient de Paiement Service, pas du montant de la facture."""

    def test_relance_2_annonce_le_reste_du_et_non_le_montant_brut(
        self, mock_fact, mock_abonne, mock_paiement, mock_wa
    ) -> None:
        """L'abonné a versé 8 000 sur 10 000 : il doit 2 000, pas 10 000."""
        fid, aid = str(uuid.uuid4()), str(uuid.uuid4())
        mock_fact.get_facture.return_value = _facture(fid, aid, jours_retard=4, montant=10000.0)
        mock_abonne.get_abonne.return_value = _abonne(aid)
        mock_paiement.get_solde_restant.return_value = 2000.0
        mock_wa.send.return_value = None

        envoi = EnvoiService().envoyer_relance(fid, aid, etape=2)

        self.assertEqual(envoi.statut, StatutEnvoi.ENVOYE)
        texte = mock_wa.send.call_args.args[1]
        self.assertIn("2000 FCFA", texte)
        self.assertNotIn("10000", texte)
        self.assertIn("depuis 4 jours", texte)

    def test_solde_illisible_le_message_part_sans_le_montant(
        self, mock_fact, mock_abonne, mock_paiement, mock_wa
    ) -> None:
        """`None` = illisible. Mieux vaut relancer sans chiffre qu'avec un faux."""
        fid, aid = str(uuid.uuid4()), str(uuid.uuid4())
        mock_fact.get_facture.return_value = _facture(fid, aid, jours_retard=4)
        mock_abonne.get_abonne.return_value = _abonne(aid)
        mock_paiement.get_solde_restant.return_value = None
        mock_wa.send.return_value = None

        EnvoiService().envoyer_relance(fid, aid, etape=2)

        texte = mock_wa.send.call_args.args[1]
        self.assertNotIn("FCFA", texte)
        self.assertIn("est impayée", texte)

    def test_suspension_lit_la_dette_totale_de_l_abonne(self, mock_fact, mock_abonne, mock_paiement, mock_wa) -> None:
        fid, aid = str(uuid.uuid4()), str(uuid.uuid4())
        mock_fact.get_facture.return_value = _facture(fid, aid, jours_retard=12, montant=10000.0)
        mock_abonne.get_abonne.return_value = _abonne(aid)
        mock_paiement.get_dette_abonne.return_value = (27500.0, 3, "2026-05-20")
        mock_wa.send.return_value = None

        envoi = EnvoiService().envoyer_relance(fid, aid, etape=4)

        self.assertEqual(envoi.type_envoi, TypeEnvoi.SUSPENSION)
        texte = mock_wa.send.call_args.args[1]
        self.assertIn("27500 FCFA", texte)
        self.assertIn("Pour être rétabli", texte)

    def test_le_delai_avant_suspension_vient_de_l_appelant(
        self, mock_fact, mock_abonne, mock_paiement, mock_wa
    ) -> None:
        fid, aid = str(uuid.uuid4()), str(uuid.uuid4())
        mock_fact.get_facture.return_value = _facture(fid, aid, jours_retard=8)
        mock_abonne.get_abonne.return_value = _abonne(aid)
        mock_paiement.get_solde_restant.return_value = 2000.0
        mock_wa.send.return_value = None

        EnvoiService().envoyer_relance(fid, aid, etape=3, jours_avant_suspension=2)

        self.assertIn("dans les 2 jours", mock_wa.send.call_args.args[1])
