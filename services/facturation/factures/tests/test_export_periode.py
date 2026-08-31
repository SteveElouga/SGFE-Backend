"""Lister les factures par PÉRIODE — et donc voir les régularisations.

Le seul filtre existant était la campagne. Deux conséquences :

  1. aucun journal mensuel ni annuel — un comptable devait exporter campagne par
     campagne et recoller les fichiers à la main ;
  2. les régularisations, créées avec `campagne_id=""`, étaient introuvables par
     ce filtre. Or c'est la seule dette qu'on saisit à la main : l'arriéré
     antérieur à la mise en service. La comptabilité exportée ne la voyait donc
     jamais.
"""

import datetime
from decimal import Decimal

from django.core.exceptions import ValidationError
from django.test import TestCase

from factures.models import Facture, NatureFacture, StatutFacture
from factures.repositories import FactureRepository
from factures.services import FactureService


def _facture(numero: str, jour: datetime.date, campagne_id: str, nature: str) -> Facture:
    f = Facture.objects.create(
        numero_facture=numero,
        abonne_id="ab-1",
        campagne_id=campagne_id,
        ancien_index=Decimal("0"),
        nouveau_index=Decimal("10"),
        consommation=Decimal("10"),
        prix_m3=Decimal("500"),
        montant=Decimal("5000"),
        statut=StatutFacture.IMPAYEE,
        date_releve=jour,
        date_limite_paiement=jour + datetime.timedelta(days=5),
        nature=nature,
    )
    # `date_generation` est `auto_now_add` : on la repositionne pour placer la
    # facture dans la période visée.
    Facture.objects.filter(pk=f.pk).update(
        date_generation=datetime.datetime.combine(jour, datetime.time(9, 0), tzinfo=datetime.UTC)
    )
    return Facture.objects.get(pk=f.pk)


class TestFiltrePeriode(TestCase):
    def setUp(self) -> None:
        self.repo = FactureRepository()
        _facture("FACT-JUIN", datetime.date(2026, 6, 15), "camp-juin", NatureFacture.CONSOMMATION)
        _facture("FACT-JUIL", datetime.date(2026, 7, 15), "camp-juillet", NatureFacture.CONSOMMATION)
        _facture("REG-JUIL", datetime.date(2026, 7, 20), "", NatureFacture.REGULARISATION)

    def _numeros(self, **kw) -> set[str]:
        return {f.numero_facture for f in self.repo.list_by_filters(**kw)}

    def test_un_mois(self) -> None:
        self.assertEqual(
            self._numeros(date_debut=datetime.date(2026, 7, 1), date_fin=datetime.date(2026, 7, 31)),
            {"FACT-JUIL", "REG-JUIL"},
        )

    def test_bornes_incluses(self) -> None:
        """Le 15 juin doit apparaître dans une période bornée au 15 juin."""
        self.assertEqual(
            self._numeros(date_debut=datetime.date(2026, 6, 15), date_fin=datetime.date(2026, 6, 15)),
            {"FACT-JUIN"},
        )

    def test_borne_seule(self) -> None:
        self.assertEqual(self._numeros(date_debut=datetime.date(2026, 7, 1)), {"FACT-JUIL", "REG-JUIL"})
        self.assertEqual(self._numeros(date_fin=datetime.date(2026, 6, 30)), {"FACT-JUIN"})

    def test_la_regularisation_est_introuvable_par_campagne_mais_visible_par_periode(self) -> None:
        """Le cœur du défaut, en deux assertions.

        Son `campagne_id` est vide : aucun filtre par campagne ne peut la
        trouver. Seule la période la voit.
        """
        par_campagne = {n for c in ("camp-juin", "camp-juillet") for n in self._numeros(campagne_id=c)}
        self.assertNotIn("REG-JUIL", par_campagne)
        self.assertIn("REG-JUIL", self._numeros(date_debut=datetime.date(2026, 1, 1)))

    def test_periode_et_campagne_se_combinent(self) -> None:
        self.assertEqual(
            self._numeros(
                campagne_id="camp-juillet",
                date_debut=datetime.date(2026, 7, 1),
                date_fin=datetime.date(2026, 7, 31),
            ),
            {"FACT-JUIL"},
        )

    def test_sans_critere_tout_sort(self) -> None:
        self.assertEqual(self._numeros(), {"FACT-JUIN", "FACT-JUIL", "REG-JUIL"})


class TestValidationDesBornes(TestCase):
    """Une borne illisible lève, elle n'est pas ignorée.

    L'ignorer rendrait tout l'historique là où le comptable a demandé un mois,
    et rien ne le lui dirait avant qu'il somme la colonne.
    """

    def setUp(self) -> None:
        # Le service construit ses clients gRPC : on ne les touche pas, aucun
        # appel réseau n'a lieu sur `list_factures`.
        self.svc = FactureService()

    def test_date_illisible_leve(self) -> None:
        with self.assertRaises(ValidationError):
            self.svc.list_factures(date_debut="01/07/2026")
        with self.assertRaises(ValidationError):
            self.svc.list_factures(date_fin="pas-une-date")

    def test_chaine_vide_signifie_pas_de_borne(self) -> None:
        self.svc.list_factures(date_debut="", date_fin="")  # ne lève pas
