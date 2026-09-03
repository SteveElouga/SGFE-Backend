"""Tests de l'imputation d'un versement au niveau abonné, du plus ancien au plus récent.

Ces tests portent sur de l'argent : chacun vérifie non seulement le solde final
mais la **répartition**, parce qu'un total juste obtenu par une mauvaise
ventilation laisse vieillir la mauvaise dette — et c'est l'ancienneté qui
déclenche les relances et la suspension.
"""

from datetime import date, timedelta
from decimal import Decimal

from django.core.exceptions import ValidationError
from django.test import TestCase

from paiements.models import AvoirAbonne, ModePaiement, Paiement, SoldeFacture, StatutSolde
from paiements.services import PaiementService

ABONNE = "abonne-fifo-001"


def _solde(facture_id: str, montant: str, jours: int, abonne: str = ABONNE) -> SoldeFacture:
    """Crée un solde exigible il y a `jours` jours (négatif = échéance future)."""
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


class ImputationFifoTest(TestCase):
    def setUp(self) -> None:
        self.service = PaiementService()

    def _encaisser(self, montant: str, reference: str = "") -> tuple[list[Paiement], Decimal]:
        return self.service.enregistrer_paiement_abonne(
            abonne_id=ABONNE,
            montant=float(Decimal(montant)),
            date_paiement=date.today(),
            mode_paiement=ModePaiement.ESPECES,
            reference_transaction=reference,
            enregistre_par="caissier-test",
        )

    def test_solde_la_dette_la_plus_ancienne_en_premier(self) -> None:
        """Un versement partiel éteint l'arriéré, pas la facture du mois."""
        _solde("ancienne", "7000", jours=60)
        _solde("recente", "26000", jours=5)

        crees, excedent = self._encaisser("7000")

        self.assertEqual(len(crees), 1)
        self.assertEqual(crees[0].facture_id, "ancienne")
        self.assertEqual(excedent, Decimal("0"))
        self.assertEqual(SoldeFacture.objects.get(facture_id="ancienne").statut, StatutSolde.PAYEE)
        self.assertEqual(SoldeFacture.objects.get(facture_id="recente").solde_restant, Decimal("26000.00"))

    def test_le_reliquat_deborde_sur_la_suivante(self) -> None:
        """Un versement qui dépasse la plus ancienne poursuit sur la suivante."""
        _solde("ancienne", "7000", jours=60)
        _solde("recente", "26000", jours=5)

        crees, excedent = self._encaisser("10000")

        self.assertEqual([p.facture_id for p in crees], ["ancienne", "recente"])
        self.assertEqual([p.montant for p in crees], [Decimal("7000.00"), Decimal("3000.00")])
        self.assertEqual(excedent, Decimal("0"))
        self.assertEqual(SoldeFacture.objects.get(facture_id="ancienne").statut, StatutSolde.PAYEE)
        self.assertEqual(SoldeFacture.objects.get(facture_id="recente").solde_restant, Decimal("23000.00"))

    def test_l_ordre_suit_l_exigibilite_pas_la_creation(self) -> None:
        """Une régularisation saisie aujourd'hui mais exigible avant passe devant.

        C'est le cas d'usage qui motive tout ceci : l'arriéré antérieur à
        l'application est créé en dernier et doit s'éteindre en premier.
        """
        _solde("facture-du-mois", "26000", jours=5)
        _solde("regularisation", "33000", jours=200)  # saisie après, exigible avant

        crees, _ = self._encaisser("33000")

        self.assertEqual([p.facture_id for p in crees], ["regularisation"])
        self.assertEqual(
            SoldeFacture.objects.get(facture_id="facture-du-mois").solde_restant,
            Decimal("26000.00"),
        )

    def test_l_excedent_part_en_avoir(self) -> None:
        """Ce qui reste après extinction de toutes les dettes est porté au crédit."""
        _solde("seule", "5000", jours=10)

        crees, excedent = self._encaisser("8000")

        self.assertEqual(len(crees), 1)
        self.assertEqual(excedent, Decimal("3000"))
        self.assertEqual(AvoirAbonne.objects.get(abonne_id=ABONNE).montant, Decimal("3000.00"))

    def test_une_reference_deja_vue_ne_credite_rien(self) -> None:
        """Idempotence : le rejeu d'un versement Mobile Money ne double pas l'encaissement."""
        _solde("seule", "5000", jours=10)
        self.service.enregistrer_paiement_abonne(
            abonne_id=ABONNE,
            montant=5000.0,
            date_paiement=date.today(),
            mode_paiement=ModePaiement.MOBILE_MONEY,
            reference_transaction="MM-123",
            enregistre_par="caissier-test",
        )
        crees, excedent = self.service.enregistrer_paiement_abonne(
            abonne_id=ABONNE,
            montant=5000.0,
            date_paiement=date.today(),
            mode_paiement=ModePaiement.MOBILE_MONEY,
            reference_transaction="MM-123",
            enregistre_par="caissier-test",
        )

        self.assertEqual(len(crees), 1)
        self.assertEqual(excedent, Decimal("0"))
        self.assertEqual(Paiement.objects.filter(abonne_id=ABONNE).count(), 1)

    def test_la_reference_ne_se_pose_que_sur_la_premiere_ecriture(self) -> None:
        """La contrainte d'unicité interdit de répéter la référence sur chaque part."""
        _solde("ancienne", "1000", jours=60)
        _solde("recente", "1000", jours=5)

        crees, _ = self.service.enregistrer_paiement_abonne(
            abonne_id=ABONNE,
            montant=2000.0,
            date_paiement=date.today(),
            mode_paiement=ModePaiement.VIREMENT,
            reference_transaction="VIR-999",
            enregistre_par="caissier-test",
        )

        self.assertEqual([p.reference_transaction for p in crees], ["VIR-999", ""])

    def test_montant_nul_ou_negatif_refuse(self) -> None:
        _solde("seule", "5000", jours=10)
        for montant in (0.0, -100.0):
            with self.assertRaises(ValidationError):
                self.service.enregistrer_paiement_abonne(
                    abonne_id=ABONNE,
                    montant=montant,
                    date_paiement=date.today(),
                    mode_paiement=ModePaiement.ESPECES,
                    reference_transaction="",
                    enregistre_par="caissier-test",
                )

    def test_reference_obligatoire_pour_mobile_money(self) -> None:
        _solde("seule", "5000", jours=10)
        with self.assertRaises(ValidationError):
            self.service.enregistrer_paiement_abonne(
                abonne_id=ABONNE,
                montant=1000.0,
                date_paiement=date.today(),
                mode_paiement=ModePaiement.MOBILE_MONEY,
                reference_transaction="   ",
                enregistre_par="caissier-test",
            )

    def test_un_abonne_sans_dette_verse_tout_en_avoir(self) -> None:
        """Sans aucune facture ouverte, tout le versement part au crédit."""
        crees, excedent = self._encaisser("4000")
        self.assertEqual(crees, [])
        self.assertEqual(excedent, Decimal("4000"))
        self.assertEqual(AvoirAbonne.objects.get(abonne_id=ABONNE).montant, Decimal("4000.00"))


class TotalDuAbonneTest(TestCase):
    def setUp(self) -> None:
        self.service = PaiementService()

    def test_somme_les_soldes_restants(self) -> None:
        _solde("a", "7000", jours=60)
        _solde("b", "26000", jours=5)
        self.assertEqual(self.service.total_du_abonne(ABONNE), Decimal("33000.00"))

    def test_exclut_la_facture_courante(self) -> None:
        """Sur une facture imprimée, le « solde antérieur » l'exclut elle-même."""
        _solde("courante", "26000", jours=5)
        _solde("anterieure", "7000", jours=60)
        self.assertEqual(self.service.total_du_abonne(ABONNE, hors_facture_id="courante"), Decimal("7000.00"))

    def test_ignore_les_factures_soldees(self) -> None:
        _solde("payee", "5000", jours=30).__class__.objects.filter(facture_id="payee").update(
            statut=StatutSolde.PAYEE, solde_restant=Decimal("0")
        )
        _solde("due", "1000", jours=10)
        self.assertEqual(self.service.total_du_abonne(ABONNE), Decimal("1000.00"))

    def test_zero_quand_rien_n_est_du(self) -> None:
        self.assertEqual(self.service.total_du_abonne("inconnu"), Decimal("0"))
