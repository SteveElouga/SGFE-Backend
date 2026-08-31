"""Annuler un versement doit défaire ce qu'il avait produit — partout.

Un versement a sept conséquences au-delà du solde de sa facture. Son annulation
n'en défaisait qu'une : le statut envoyé à Facturation. Trois manques coûtaient
de l'argent ou du recouvrement, et deux d'entre eux ont été reproduits en
exécution avant correction :

  facture 5 000 · versé 10 000 · annulé
    → l'abonné doit 5 000 ET conserve 5 000 d'avoir
    → il a versé 10 000, on lui rend 10 000, et il garde 5 000 de crédit

Ces tests fixent les trois : le solde ne rétablit que la part imputée, l'avoir
est repris, et le suivi d'impayé est rouvert pour que le cron relance à nouveau.
"""

from datetime import date, timedelta

from django.core.exceptions import ValidationError
from django.test import TestCase

from paiements.models import ModePaiement, StatutSolde, TypeMouvementAvoir
from paiements.services import PaiementService

ABONNE = "abonne-test"
DEMAIN = date.today() + timedelta(days=5)


class SoldeRetabliDeLaPartImputee(TestCase):
    """Le solde ne doit rendre que ce qui l'avait touché."""

    def setUp(self):
        self.svc = PaiementService()

    def test_un_versement_exact_rend_tout(self):
        self.svc.initialiser_solde("f-1", ABONNE, 5000, DEMAIN)
        p, _ = self.svc.enregistrer_paiement("f-1", ABONNE, 5000, date.today(), ModePaiement.ESPECES, "", "caissier")

        _, solde = self.svc.annuler_paiement(str(p.id), "erreur de saisie", "admin")

        self.assertEqual(solde.solde_restant, 5000)
        self.assertEqual(solde.statut, StatutSolde.IMPAYEE)

    def test_un_trop_percu_ne_rend_que_la_part_imputee(self):
        # C'est ici que l'ancien code s'appuyait sur un garde-fou anti-négatif
        # au lieu d'être juste : il retirait 10 000 d'un solde qui n'en avait
        # reçu que 5 000, puis remontait le résultat à zéro.
        self.svc.initialiser_solde("f-1", ABONNE, 5000, DEMAIN)
        p, _ = self.svc.enregistrer_paiement("f-1", ABONNE, 10000, date.today(), ModePaiement.ESPECES, "", "caissier")
        # `montant` porte la part imputée à cette facture, pas la somme reçue :
        # une écriture par facture touchée. L'excédent, lui, n'a nulle part où
        # cascader ici — c'est la seule dette de l'abonné.
        self.assertEqual(p.montant, 5000)
        self.assertEqual(p.montant_excedent, 5000)

        _, solde = self.svc.annuler_paiement(str(p.id), "erreur", "admin")

        self.assertEqual(solde.solde_restant, 5000)
        self.assertEqual(solde.montant_paye, 0)

    def test_un_versement_partiel_laisse_le_reste_du(self):
        self.svc.initialiser_solde("f-1", ABONNE, 20500, DEMAIN)
        p, _ = self.svc.enregistrer_paiement("f-1", ABONNE, 8000, date.today(), ModePaiement.ESPECES, "", "caissier")

        _, solde = self.svc.annuler_paiement(str(p.id), "erreur", "admin")

        self.assertEqual(solde.solde_restant, 20500)
        self.assertEqual(solde.statut, StatutSolde.IMPAYEE)


class AvoirRepris(TestCase):
    """L'excédent porté au crédit repart avec le versement qui l'a produit."""

    def setUp(self):
        self.svc = PaiementService()

    def test_l_avoir_est_debite_de_l_excedent(self):
        self.svc.initialiser_solde("f-1", ABONNE, 5000, DEMAIN)
        p, _ = self.svc.enregistrer_paiement("f-1", ABONNE, 10000, date.today(), ModePaiement.ESPECES, "", "caissier")
        avoir_avant, _ = self.svc.get_avoir_abonne(ABONNE)
        self.assertEqual(avoir_avant, 5000)

        self.svc.annuler_paiement(str(p.id), "erreur", "admin")

        avoir_apres, mouvements = self.svc.get_avoir_abonne(ABONNE)
        self.assertEqual(avoir_apres, 0)
        # La reprise est tracée, et distincte d'une ANNULATION de facture — qui
        # est un crédit, pas un débit.
        types = [m.type_mouvement for m in mouvements]
        self.assertIn(TypeMouvementAvoir.REPRISE_TROP_PERCU, types)

    def test_un_versement_sans_excedent_ne_touche_pas_l_avoir(self):
        self.svc.crediter_avoir_manuel(ABONNE, 3000, "geste commercial", "admin")
        self.svc.initialiser_solde("f-1", ABONNE, 20500, DEMAIN)
        # L'avoir s'est imputé à la création : 20 500 − 3 000 = 17 500 restants.
        p, _ = self.svc.enregistrer_paiement("f-1", ABONNE, 5000, date.today(), ModePaiement.ESPECES, "", "caissier")

        self.svc.annuler_paiement(str(p.id), "erreur", "admin")

        avoir, _ = self.svc.get_avoir_abonne(ABONNE)
        # L'avoir manuel a été consommé par la facture, pas par le versement :
        # annuler le versement ne doit rien lui reprendre.
        self.assertEqual(avoir, 0)

    def test_refuse_l_annulation_quand_l_excedent_est_deja_depense(self):
        """Le cas qu'on ne peut pas corriger à moitié.

        L'excédent a été consommé par la facture suivante à sa naissance. Le
        reprendre demanderait de remonter la chaîne d'imputation — rétablir un
        solde peut-être déjà relancé, voire soldé par d'autres versements.

        Un refus explicite vaut mieux qu'un solde faux : avant correction, ce
        scénario laissait l'abonné devoir 8 000 là où il devait 13 000.
        """
        self.svc.initialiser_solde("f-1", ABONNE, 5000, DEMAIN)
        p, _ = self.svc.enregistrer_paiement("f-1", ABONNE, 10000, date.today(), ModePaiement.ESPECES, "", "caissier")
        # La facture suivante consomme l'avoir à sa création.
        self.svc.initialiser_solde("f-2", ABONNE, 8000, date.today() + timedelta(days=35))
        avoir, _ = self.svc.get_avoir_abonne(ABONNE)
        self.assertEqual(avoir, 0)

        with self.assertRaises(ValidationError) as ctx:
            self.svc.annuler_paiement(str(p.id), "erreur", "admin")

        message = str(ctx.exception)
        self.assertIn("déjà été imputé", message)

        # Et rien n'a bougé : le refus est total, pas partiel.
        self.assertEqual(self.svc.get_solde("f-1").solde_restant, 0)
        self.assertEqual(self.svc.get_solde("f-2").solde_restant, 3000)
        self.assertFalse(self.svc._paiement_repo.get_by_id(str(p.id)).annule)


class SuiviImpayeRouvert(TestCase):
    """Une dette rétablie doit redevenir relançable."""

    def setUp(self):
        self.svc = PaiementService()

    def _suivi_resolu(self, facture_id, abonne_id):
        """Crée un suivi d'impayé déjà résolu, comme après un paiement complet."""
        from paiements.models import SuiviImpaye

        return SuiviImpaye.objects.create(
            facture_id=facture_id,
            abonne_id=abonne_id,
            date_depassement=date.today() - timedelta(days=10),
            etape_actuelle=4,
            resolu_le=date.today(),
        )

    def test_le_suivi_est_rouvert_quand_la_facture_redevient_impayee(self):
        # C'était le manque le plus coûteux : `resolu_le` restait daté, le cron
        # de 8 h sautait la facture, et la dette vieillissait sans relance.
        self.svc.initialiser_solde("f-1", ABONNE, 5000, DEMAIN)
        p, _ = self.svc.enregistrer_paiement("f-1", ABONNE, 5000, date.today(), ModePaiement.ESPECES, "", "caissier")
        suivi = self._suivi_resolu("f-1", ABONNE)
        self.assertIsNotNone(suivi.resolu_le)

        self.svc.annuler_paiement(str(p.id), "erreur", "admin")

        suivi.refresh_from_db()
        self.assertIsNone(suivi.resolu_le)

    def test_le_suivi_reste_resolu_si_la_facture_est_encore_soldee(self):
        # Deux versements, on n'annule que le second : la facture reste payée
        # par le premier, il n'y a rien à relancer.
        self.svc.initialiser_solde("f-1", ABONNE, 5000, DEMAIN)
        self.svc.enregistrer_paiement("f-1", ABONNE, 5000, date.today(), ModePaiement.ESPECES, "", "caissier")
        p2, _ = self.svc.enregistrer_paiement("f-1", ABONNE, 2000, date.today(), ModePaiement.ESPECES, "", "caissier")
        suivi = self._suivi_resolu("f-1", ABONNE)

        # p2 n'a rien pu imputer (facture déjà soldée, aucun autre impayé) :
        # son écriture vaut zéro et porte les 2 000 partis à l'avoir.
        self.assertEqual(p2.montant, 0)
        p2.refresh_from_db()
        self.assertEqual(p2.montant_excedent, 2000)
        _, solde = self.svc.annuler_paiement(str(p2.id), "doublon", "admin")

        self.assertEqual(solde.statut, StatutSolde.PAYEE)
        suivi.refresh_from_db()
        self.assertIsNotNone(suivi.resolu_le)

    def test_une_facture_jamais_tombee_en_impaye_n_a_rien_a_rouvrir(self):
        self.svc.initialiser_solde("f-1", ABONNE, 5000, DEMAIN)
        p, _ = self.svc.enregistrer_paiement("f-1", ABONNE, 5000, date.today(), ModePaiement.ESPECES, "", "caissier")

        # Ne doit pas lever : l'absence de suivi est le cas normal.
        _, solde = self.svc.annuler_paiement(str(p.id), "erreur", "admin")
        self.assertEqual(solde.statut, StatutSolde.IMPAYEE)


class VersementAbonneMultiFactures(TestCase):
    """Un versement réparti sur plusieurs factures : l'excédent tient à la dernière écriture."""

    def setUp(self):
        self.svc = PaiementService()

    def test_l_excedent_se_rattache_a_la_derniere_ecriture(self):
        self.svc.initialiser_solde("f-1", ABONNE, 3000, date.today() - timedelta(days=30))
        self.svc.initialiser_solde("f-2", ABONNE, 5000, DEMAIN)

        crees, restant = self.svc.enregistrer_paiement_abonne(
            ABONNE, 10000, date.today(), ModePaiement.ESPECES, "", "caissier"
        )

        self.assertEqual(len(crees), 2)
        self.assertEqual(restant, 2000)
        # Chaque écriture ne rend que ce qu'elle a imputé ; seule la dernière
        # porte l'excédent.
        self.assertEqual(crees[0].montant_excedent, 0)
        crees[1].refresh_from_db()
        self.assertEqual(crees[1].montant_excedent, 2000)

    def test_annuler_une_ecriture_annule_tout_le_versement(self):
        self.svc.initialiser_solde("f-1", ABONNE, 3000, date.today() - timedelta(days=30))
        self.svc.initialiser_solde("f-2", ABONNE, 5000, DEMAIN)
        crees, _ = self.svc.enregistrer_paiement_abonne(
            ABONNE, 10000, date.today(), ModePaiement.ESPECES, "", "caissier"
        )

        # On clique sur la PREMIÈRE écriture — l'annulation défait le versement
        # entier. N'en défaire qu'une laisserait les autres imputations debout.
        _, solde = self.svc.annuler_paiement(str(crees[0].id), "erreur", "admin")

        self.assertEqual(solde.solde_restant, 3000)
        self.assertEqual(self.svc.get_solde("f-2").solde_restant, 5000)
        avoir_apres, _ = self.svc.get_avoir_abonne(ABONNE)
        self.assertEqual(avoir_apres, 0)
