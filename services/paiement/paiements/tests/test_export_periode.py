"""Lister les paiements par PÉRIODE — le journal de caisse.

`ListPaiementsParCampagne` remonte des `SoldeFacture` filtrés par `campagne_id`.
Un paiement de régularisation a un `SoldeFacture` dont le `campagne_id` est vide :
ce chemin ne pouvait donc jamais le trouver. Et il n'existait aucun moyen
d'obtenir les versements d'un mois.

Le filtre porte sur `Paiement.date_paiement` — la date de caisse, celle qu'un
journal demande, et celle que tout versement possède.
"""

from datetime import date
from decimal import Decimal

from django.core.exceptions import ValidationError
from django.test import TestCase

from paiements.models import ModePaiement
from paiements.repositories import PaiementRepository, SoldeFactureRepository
from paiements.services import PaiementService

ABONNE = "ab-1"


def _paiement(facture_id: str, jour: date, montant: str = "5000") -> None:
    PaiementRepository().create(
        facture_id=facture_id,
        abonne_id=ABONNE,
        montant=Decimal(montant),
        date_paiement=jour,
        mode_paiement=ModePaiement.ESPECES,
        reference_transaction="",
        enregistre_par="caissier",
    )


class TestFiltrePeriode(TestCase):
    def setUp(self) -> None:
        self.repo = PaiementRepository()
        solde_repo = SoldeFactureRepository()
        # Une facture de campagne, et une régularisation (campagne_id vide).
        solde_repo.create(
            facture_id="fac-camp",
            abonne_id=ABONNE,
            montant_total=Decimal("5000"),
            date_limite_paiement=date(2026, 7, 20),
            campagne_id="camp-juillet",
        )
        solde_repo.create(
            facture_id="fac-reg",
            abonne_id=ABONNE,
            montant_total=Decimal("5000"),
            date_limite_paiement=date(2026, 7, 25),
            campagne_id="",
        )
        _paiement("fac-camp", date(2026, 6, 10))
        _paiement("fac-camp", date(2026, 7, 10))
        _paiement("fac-reg", date(2026, 7, 22))

    def _jours(self, **kw) -> list[date]:
        return [p.date_paiement for p in self.repo.list_by_facture_and_abonne("", "", **kw)]

    def test_un_mois(self) -> None:
        self.assertEqual(
            self._jours(date_debut=date(2026, 7, 1), date_fin=date(2026, 7, 31)), [date(2026, 7, 10), date(2026, 7, 22)]
        )

    def test_bornes_incluses(self) -> None:
        self.assertEqual(self._jours(date_debut=date(2026, 7, 10), date_fin=date(2026, 7, 10)), [date(2026, 7, 10)])

    def test_tri_chronologique_des_qu_une_borne_est_posee(self) -> None:
        """Un journal se lit dans l'ordre où l'argent est entré."""
        jours = self._jours(date_debut=date(2026, 1, 1))
        self.assertEqual(jours, sorted(jours))

    def test_le_paiement_de_regularisation_est_vu_par_la_periode(self) -> None:
        """Il ne l'était par aucun chemin.

        `list_by_campagne` remonte les `SoldeFacture` d'une campagne ; celui
        d'une régularisation porte un `campagne_id` vide.
        """
        par_campagne = {p.facture_id for p in self.repo.list_by_campagne("camp-juillet")}
        self.assertNotIn("fac-reg", par_campagne)

        par_periode = {p.facture_id for p in self.repo.list_by_facture_and_abonne("", "", date_debut=date(2026, 7, 1))}
        self.assertIn("fac-reg", par_periode)

    def test_sans_borne_le_comportement_est_inchange(self) -> None:
        self.assertEqual(len(self._jours()), 3)


class TestValidationDesBornes(TestCase):
    def test_date_illisible_leve(self) -> None:
        svc = PaiementService()
        with self.assertRaises(ValidationError):
            svc.list_paiements(date_debut="10/07/2026")
        with self.assertRaises(ValidationError):
            svc.list_paiements(date_fin="pas-une-date")

    def test_chaine_vide_signifie_pas_de_borne(self) -> None:
        PaiementService().list_paiements(date_debut="", date_fin="")  # ne lève pas
