"""Annulation d'une facture : ce que devient l'argent déjà versé.

Une facture s'annule le plus souvent après qu'on a commencé à la payer — c'est
même la situation typique, puisqu'une erreur d'index se découvre quand
quelqu'un vient régler et conteste son montant. Ce qu'il a versé ne lui
appartient pas moins parce que la facture disparaît.

Deux exigences se croisent ici. L'argent doit revenir à l'abonné, sous forme
d'avoir imputable sur la suite. Et le solde doit cesser d'exister comme dette,
sans pour autant être compté comme payé : « payée » et « annulée » racontent
deux histoires opposées, et les confondre ferait entrer dans les recettes une
somme que personne n'a versée.
"""

from datetime import date, timedelta
from decimal import Decimal

from django.test import TestCase

from paiements.models import (
    AvoirAbonne,
    ModePaiement,
    MouvementAvoir,
    SoldeFacture,
    StatutSolde,
    TypeMouvementAvoir,
)
from paiements.services import PaiementService

ABONNE = "abonne-annul-001"


def _solde(facture_id: str, montant: str, jours: int = 10, abonne: str = ABONNE) -> SoldeFacture:
    m = Decimal(montant)
    return SoldeFacture.objects.create(
        facture_id=facture_id,
        abonne_id=abonne,
        montant_total=m,
        montant_paye=Decimal("0"),
        solde_restant=m,
        statut=StatutSolde.IMPAYEE,
        date_limite_paiement=date.today() - timedelta(days=jours),
    )


class AnnulerSoldeTest(TestCase):
    def setUp(self) -> None:
        self.service = PaiementService()

    def test_une_facture_intacte_s_annule_sans_rien_crediter(self) -> None:
        _solde("f-1", "10000")
        solde, credite = self.service.annuler_solde("f-1", motif="Erreur d'index")
        self.assertEqual(solde.statut, StatutSolde.ANNULEE)
        self.assertEqual(solde.solde_restant, Decimal("0"))
        self.assertEqual(credite, Decimal("0"))
        self.assertIsNone(AvoirAbonne.objects.filter(abonne_id=ABONNE).first())

    def test_ce_qui_a_ete_verse_revient_a_l_abonne(self) -> None:
        _solde("f-1", "10000")
        self.service.enregistrer_paiement(
            facture_id="f-1",
            abonne_id=ABONNE,
            montant=4000,
            date_paiement=date.today(),
            mode_paiement=ModePaiement.ESPECES,
            reference_transaction="",
            enregistre_par="caissier",
        )
        _solde_apres, credite = self.service.annuler_solde("f-1", motif="Erreur d'index")
        self.assertEqual(credite, Decimal("4000"))
        avoir = AvoirAbonne.objects.get(abonne_id=ABONNE)
        self.assertEqual(avoir.montant, Decimal("4000"))

    def test_une_facture_entierement_payee_rend_tout(self) -> None:
        _solde("f-1", "10000")
        self.service.enregistrer_paiement(
            facture_id="f-1",
            abonne_id=ABONNE,
            montant=10000,
            date_paiement=date.today(),
            mode_paiement=ModePaiement.ESPECES,
            reference_transaction="",
            enregistre_par="caissier",
        )
        _s, credite = self.service.annuler_solde("f-1", motif="Doublon")
        self.assertEqual(credite, Decimal("10000"))

    def test_le_mouvement_est_trace_comme_annulation_pas_comme_trop_percu(self) -> None:
        """L'abonné n'a pas versé de trop : c'est la facture qui a disparu."""
        _solde("f-1", "10000")
        self.service.enregistrer_paiement(
            facture_id="f-1",
            abonne_id=ABONNE,
            montant=4000,
            date_paiement=date.today(),
            mode_paiement=ModePaiement.ESPECES,
            reference_transaction="",
            enregistre_par="caissier",
        )
        self.service.annuler_solde("f-1", motif="Erreur d'index")
        mouvements = MouvementAvoir.objects.filter(abonne_id=ABONNE, type_mouvement=TypeMouvementAvoir.ANNULATION)
        self.assertEqual(mouvements.count(), 1)
        premier = mouvements.first()
        assert premier is not None
        self.assertEqual(premier.facture_id, "f-1")

    def test_annuler_deux_fois_ne_credite_qu_une_fois(self) -> None:
        """Un double-clic ne doit pas doubler l'avoir."""
        _solde("f-1", "10000")
        self.service.enregistrer_paiement(
            facture_id="f-1",
            abonne_id=ABONNE,
            montant=4000,
            date_paiement=date.today(),
            mode_paiement=ModePaiement.ESPECES,
            reference_transaction="",
            enregistre_par="caissier",
        )
        self.service.annuler_solde("f-1", motif="Erreur")
        _s, credite2 = self.service.annuler_solde("f-1", motif="Erreur")
        self.assertEqual(credite2, Decimal("0"))
        self.assertEqual(AvoirAbonne.objects.get(abonne_id=ABONNE).montant, Decimal("4000"))

    def test_annulee_n_est_pas_payee(self) -> None:
        """Confondre les deux ferait entrer dans les recettes ce que nul n'a versé."""
        _solde("f-1", "10000")
        solde, _ = self.service.annuler_solde("f-1", motif="Erreur")
        self.assertNotEqual(solde.statut, StatutSolde.PAYEE)
        self.assertEqual(solde.statut, StatutSolde.ANNULEE)

    def test_un_solde_annule_sort_des_impayes(self) -> None:
        _solde("f-1", "10000", jours=40)
        self.assertEqual(len(self.service._solde_repo.list_impayes()), 1)
        self.service.annuler_solde("f-1", motif="Erreur")
        self.assertEqual(self.service._solde_repo.list_impayes(), [])

    def test_un_solde_annule_sort_de_la_dette_de_l_abonne(self) -> None:
        _solde("f-1", "10000")
        _solde("f-2", "3000")
        self.assertEqual(self.service._solde_repo.total_du_abonne(ABONNE), Decimal("13000"))
        self.service.annuler_solde("f-1", motif="Erreur")
        self.assertEqual(self.service._solde_repo.total_du_abonne(ABONNE), Decimal("3000"))

    def test_un_versement_ulterieur_ne_s_impute_plus_dessus(self) -> None:
        """La dette annulée ne doit plus capter l'argent, même la plus ancienne."""
        _solde("annulee", "10000", jours=90)  # la plus ancienne
        _solde("vivante", "5000", jours=10)
        self.service.annuler_solde("annulee", motif="Erreur")

        self.service.enregistrer_paiement_abonne(
            abonne_id=ABONNE,
            montant=5000,
            date_paiement=date.today(),
            mode_paiement=ModePaiement.ESPECES,
            reference_transaction="",
            enregistre_par="caissier",
        )
        vivante = SoldeFacture.objects.get(facture_id="vivante")
        self.assertEqual(vivante.solde_restant, Decimal("0"))

    def test_l_avoir_rendu_s_impute_sur_la_facture_suivante(self) -> None:
        """Bout à bout : c'est le comportement que l'abonné constate."""
        _solde("f-1", "10000")
        self.service.enregistrer_paiement(
            facture_id="f-1",
            abonne_id=ABONNE,
            montant=6000,
            date_paiement=date.today(),
            mode_paiement=ModePaiement.ESPECES,
            reference_transaction="",
            enregistre_par="caissier",
        )
        self.service.annuler_solde("f-1", motif="Erreur d'index")

        # La facture corrigée arrive.
        nouveau = self.service.initialiser_solde(
            facture_id="f-2",
            abonne_id=ABONNE,
            campagne_id="c-1",
            montant_total=8000,
            date_limite_paiement=date.today() + timedelta(days=15),
        )
        # Les 6 000 déjà versés se sont imputés d'eux-mêmes.
        self.assertEqual(nouveau.montant_paye, Decimal("6000"))
        self.assertEqual(nouveau.solde_restant, Decimal("2000"))
        self.assertEqual(AvoirAbonne.objects.get(abonne_id=ABONNE).montant, Decimal("0"))
