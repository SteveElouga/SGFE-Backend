"""Il ne peut y avoir d'avoir que si tous les impayés sont soldés.

Un abonné paie ce qu'on lui a demandé : la facture du mois dont il a reçu le
message. Le versement s'impute donc d'abord sur celle-là — puis, s'il dépasse,
sur ses impayés, du plus anciennement exigible au plus récent. Ce qui reste
après extinction de toute dette, et seulement lui, devient un crédit.

Avant cette règle, un versement de 10 000 sur une facture de 5 000 créditait
5 000 à un abonné qui devait encore 3 000 par ailleurs : il restait relancé, et
suspendu à terme, pour une dette que son propre argent couvrait déjà.

Un versement produit donc plusieurs écritures. Elles partagent un
`versement_id`, et c'est ce qui permet de les annuler d'un bloc — sans quoi
annuler « le paiement » n'en défairait qu'une, laissant les autres imputations
debout.
"""

from datetime import date, timedelta

from django.core.exceptions import ValidationError
from django.test import TestCase

from paiements.models import ModePaiement, StatutSolde, TypeMouvementAvoir
from paiements.services import PaiementService

ABONNE = "abonne-test"


def _hier(jours: int = 30) -> date:
    return date.today() - timedelta(days=jours)


def _demain(jours: int = 5) -> date:
    return date.today() + timedelta(days=jours)


class LaFactureViseeDabord(TestCase):
    def setUp(self):
        self.svc = PaiementService()

    def test_le_versement_eteint_la_facture_visee_avant_les_impayes(self):
        # Deux dettes : un arriéré de juillet et la facture d'août. L'abonné
        # règle celle d'août — c'est le message qu'il a reçu.
        self.svc.initialiser_solde("juillet", ABONNE, 3000, _hier())
        self.svc.initialiser_solde("aout", ABONNE, 5000, _demain())

        self.svc.enregistrer_paiement("aout", ABONNE, 5000, date.today(), ModePaiement.ESPECES, "", "caissier")

        self.assertEqual(self.svc.get_solde("aout").solde_restant, 0)
        # L'arriéré n'a pas été servi : le versement suffisait juste pour août.
        self.assertEqual(self.svc.get_solde("juillet").solde_restant, 3000)

    def test_l_excedent_part_sur_les_impayes_pas_a_l_avoir(self):
        # Le cœur de la règle.
        self.svc.initialiser_solde("juillet", ABONNE, 3000, _hier())
        self.svc.initialiser_solde("aout", ABONNE, 5000, _demain())

        self.svc.enregistrer_paiement("aout", ABONNE, 10000, date.today(), ModePaiement.ESPECES, "", "caissier")

        self.assertEqual(self.svc.get_solde("aout").solde_restant, 0)
        self.assertEqual(self.svc.get_solde("juillet").solde_restant, 0)
        # 5 000 pour août, 3 000 pour juillet, 2 000 en crédit.
        avoir, _ = self.svc.get_avoir_abonne(ABONNE)
        self.assertEqual(avoir, 2000)

    def test_aucun_avoir_tant_qu_il_reste_une_dette(self):
        self.svc.initialiser_solde("juillet", ABONNE, 8000, _hier())
        self.svc.initialiser_solde("aout", ABONNE, 5000, _demain())

        self.svc.enregistrer_paiement("aout", ABONNE, 10000, date.today(), ModePaiement.ESPECES, "", "caissier")

        avoir, _ = self.svc.get_avoir_abonne(ABONNE)
        self.assertEqual(avoir, 0)
        # 5 000 sur août, 5 000 sur juillet, qui en devait 8 000.
        self.assertEqual(self.svc.get_solde("juillet").solde_restant, 3000)
        self.assertEqual(self.svc.get_solde("juillet").statut, StatutSolde.PARTIELLE)

    def test_les_impayes_sont_servis_du_plus_ancien_au_plus_recent(self):
        # L'ancienneté déclenche relances et suspension : éteindre la dette
        # récente en laissant vieillir l'ancienne serait le mauvais ordre.
        self.svc.initialiser_solde("mai", ABONNE, 2000, _hier(90))
        self.svc.initialiser_solde("juin", ABONNE, 2000, _hier(60))
        self.svc.initialiser_solde("aout", ABONNE, 1000, _demain())

        self.svc.enregistrer_paiement("aout", ABONNE, 4000, date.today(), ModePaiement.ESPECES, "", "caissier")

        self.assertEqual(self.svc.get_solde("aout").solde_restant, 0)
        self.assertEqual(self.svc.get_solde("mai").solde_restant, 0)
        self.assertEqual(self.svc.get_solde("juin").solde_restant, 1000)

    def test_une_facture_deja_soldee_ne_bloque_pas_la_cascade(self):
        # Payer une facture déjà éteinte : tout part sur les impayés.
        self.svc.initialiser_solde("juillet", ABONNE, 3000, _hier())
        self.svc.initialiser_solde("aout", ABONNE, 5000, _demain())
        self.svc.enregistrer_paiement("aout", ABONNE, 5000, date.today(), ModePaiement.ESPECES, "", "caissier")

        self.svc.enregistrer_paiement("aout", ABONNE, 3000, date.today(), ModePaiement.ESPECES, "", "caissier")

        self.assertEqual(self.svc.get_solde("juillet").solde_restant, 0)
        avoir, _ = self.svc.get_avoir_abonne(ABONNE)
        self.assertEqual(avoir, 0)


class EcrituresGroupeesParVersement(TestCase):
    def setUp(self):
        self.svc = PaiementService()

    def test_un_versement_reparti_partage_un_versement_id(self):
        self.svc.initialiser_solde("juillet", ABONNE, 3000, _hier())
        self.svc.initialiser_solde("aout", ABONNE, 5000, _demain())

        p, _ = self.svc.enregistrer_paiement("aout", ABONNE, 10000, date.today(), ModePaiement.ESPECES, "", "caissier")

        ecritures = self.svc._paiement_repo.list_du_versement(p.versement_id)
        self.assertEqual(len(ecritures), 2)
        self.assertEqual({e.facture_id for e in ecritures}, {"aout", "juillet"})
        # Chaque écriture ne porte que ce qu'elle a imputé.
        montants = {e.facture_id: e.montant for e in ecritures}
        self.assertEqual(montants["aout"], 5000)
        self.assertEqual(montants["juillet"], 3000)

    def test_l_excedent_ne_se_pose_que_sur_la_derniere_ecriture(self):
        self.svc.initialiser_solde("juillet", ABONNE, 3000, _hier())
        self.svc.initialiser_solde("aout", ABONNE, 5000, _demain())

        p, _ = self.svc.enregistrer_paiement("aout", ABONNE, 10000, date.today(), ModePaiement.ESPECES, "", "caissier")

        ecritures = self.svc._paiement_repo.list_du_versement(p.versement_id)
        excedents = [e.montant_excedent for e in ecritures]
        self.assertEqual(sum(excedents), 2000)
        # Une seule écriture le porte — sinon l'annulation reprendrait le crédit
        # plusieurs fois.
        self.assertEqual(len([e for e in excedents if e > 0]), 1)

    def test_deux_versements_ne_partagent_pas_leur_identifiant(self):
        self.svc.initialiser_solde("aout", ABONNE, 20000, _demain())
        p1, _ = self.svc.enregistrer_paiement("aout", ABONNE, 5000, date.today(), ModePaiement.ESPECES, "", "c")
        p2, _ = self.svc.enregistrer_paiement("aout", ABONNE, 5000, date.today(), ModePaiement.ESPECES, "", "c")

        self.assertNotEqual(p1.versement_id, p2.versement_id)


class AnnulationDuVersementEntier(TestCase):
    def setUp(self):
        self.svc = PaiementService()

    def test_annuler_defait_toutes_les_imputations_du_versement(self):
        # Sans le regroupement, annuler l'écriture d'août aurait laissé les
        # 3 000 de juillet imputés — un solde faux, exactement le défaut qu'on
        # venait de corriger sur le trop-perçu.
        self.svc.initialiser_solde("juillet", ABONNE, 3000, _hier())
        self.svc.initialiser_solde("aout", ABONNE, 5000, _demain())
        p, _ = self.svc.enregistrer_paiement("aout", ABONNE, 10000, date.today(), ModePaiement.ESPECES, "", "caissier")

        self.svc.annuler_paiement(str(p.id), "erreur de saisie", "admin")

        self.assertEqual(self.svc.get_solde("aout").solde_restant, 5000)
        self.assertEqual(self.svc.get_solde("juillet").solde_restant, 3000)
        avoir, _ = self.svc.get_avoir_abonne(ABONNE)
        self.assertEqual(avoir, 0)

    def test_toutes_les_ecritures_sont_marquees_annulees(self):
        self.svc.initialiser_solde("juillet", ABONNE, 3000, _hier())
        self.svc.initialiser_solde("aout", ABONNE, 5000, _demain())
        p, _ = self.svc.enregistrer_paiement("aout", ABONNE, 10000, date.today(), ModePaiement.ESPECES, "", "caissier")

        self.svc.annuler_paiement(str(p.id), "erreur", "admin")

        ecritures = self.svc._paiement_repo.list_du_versement(p.versement_id)
        self.assertTrue(all(e.annule for e in ecritures))
        self.assertTrue(all(e.motif_annulation == "erreur" for e in ecritures))

    def test_annuler_depuis_n_importe_quelle_ecriture_donne_le_meme_resultat(self):
        self.svc.initialiser_solde("juillet", ABONNE, 3000, _hier())
        self.svc.initialiser_solde("aout", ABONNE, 5000, _demain())
        p, _ = self.svc.enregistrer_paiement("aout", ABONNE, 10000, date.today(), ModePaiement.ESPECES, "", "caissier")
        ecritures = self.svc._paiement_repo.list_du_versement(p.versement_id)
        celle_de_juillet = next(e for e in ecritures if e.facture_id == "juillet")

        # On clique sur l'écriture de juillet, pas sur celle qu'on a créée.
        self.svc.annuler_paiement(str(celle_de_juillet.id), "erreur", "admin")

        self.assertEqual(self.svc.get_solde("aout").solde_restant, 5000)
        self.assertEqual(self.svc.get_solde("juillet").solde_restant, 3000)

    def test_l_avoir_est_repris_une_seule_fois(self):
        self.svc.initialiser_solde("juillet", ABONNE, 3000, _hier())
        self.svc.initialiser_solde("aout", ABONNE, 5000, _demain())
        p, _ = self.svc.enregistrer_paiement("aout", ABONNE, 12000, date.today(), ModePaiement.ESPECES, "", "caissier")
        avoir_avant, _ = self.svc.get_avoir_abonne(ABONNE)
        self.assertEqual(avoir_avant, 4000)

        self.svc.annuler_paiement(str(p.id), "erreur", "admin")

        avoir_apres, mouvements = self.svc.get_avoir_abonne(ABONNE)
        self.assertEqual(avoir_apres, 0)
        reprises = [m for m in mouvements if m.type_mouvement == TypeMouvementAvoir.REPRISE_TROP_PERCU]
        self.assertEqual(len(reprises), 1)

    def test_refuse_si_l_avoir_du_versement_est_deja_depense(self):
        self.svc.initialiser_solde("aout", ABONNE, 5000, _demain())
        p, _ = self.svc.enregistrer_paiement("aout", ABONNE, 10000, date.today(), ModePaiement.ESPECES, "", "caissier")
        # La facture suivante consomme l'avoir à sa naissance.
        self.svc.initialiser_solde("septembre", ABONNE, 8000, _demain(35))

        with self.assertRaises(ValidationError):
            self.svc.annuler_paiement(str(p.id), "erreur", "admin")

        # Refus total : rien n'a bougé.
        self.assertEqual(self.svc.get_solde("aout").solde_restant, 0)
        self.assertEqual(self.svc.get_solde("septembre").solde_restant, 3000)
