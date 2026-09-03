"""Tests des modèles Tarif et Facture."""

import datetime
import uuid
from decimal import Decimal
from typing import Any

from django.test import TestCase

from factures.models import Facture, StatutFacture, Tarif


class TarifModelTests(TestCase):
    def test_creation_tarif(self) -> None:
        tarif = Tarif.objects.create(
            prix_m3=Decimal("500.00"),
            date_effet=datetime.date(2025, 7, 1),
            is_active=True,
        )
        self.assertIsNotNone(tarif.id)
        self.assertTrue(tarif.is_active)
        self.assertEqual(tarif.prix_m3, Decimal("500.00"))

    def test_str_tarif(self) -> None:
        tarif = Tarif(
            prix_m3=Decimal("500.00"),
            date_effet=datetime.date(2025, 7, 1),
            is_active=True,
        )
        self.assertIn("500", str(tarif))
        self.assertIn("actif", str(tarif))


class FactureModelTests(TestCase):
    def _make_facture(self, **kwargs: Any) -> Facture:
        defaults = dict(
            numero_facture=f"FACT-2025-07-{uuid.uuid4().int % 9999:04d}",
            abonne_id=str(uuid.uuid4()),
            campagne_id=str(uuid.uuid4()),
            ancien_index=Decimal("100.000"),
            nouveau_index=Decimal("115.000"),
            consommation=Decimal("15.000"),
            prix_m3=Decimal("500.00"),
            montant=Decimal("7500.00"),
            statut=StatutFacture.IMPAYEE,
            date_releve=datetime.date(2025, 7, 15),
            date_limite_paiement=datetime.date(2025, 7, 20),
        )
        defaults.update(kwargs)
        return Facture.objects.create(**defaults)

    def test_creation_facture(self) -> None:
        facture = self._make_facture()
        self.assertIsNotNone(facture.id)
        self.assertEqual(facture.statut, StatutFacture.IMPAYEE)
        self.assertEqual(facture.montant, Decimal("7500.00"))

    def test_str_facture(self) -> None:
        facture = self._make_facture(numero_facture="FACT-2025-07-0001")
        self.assertIn("FACT-2025-07-0001", str(facture))

    def test_numero_facture_unique(self) -> None:
        from django.db import IntegrityError

        self._make_facture(numero_facture="FACT-2025-07-0001")
        with self.assertRaises(IntegrityError):
            self._make_facture(numero_facture="FACT-2025-07-0001")


class FactureRepositoryNumerotationTests(TestCase):
    """Régression ANO-007 : la numérotation séquentielle repose sur le
    dernier numéro existant (verrouillable via select_for_update), pas sur
    un simple COUNT() qui pourrait rejouer un numéro déjà utilisé si une
    facture intermédiaire était un jour supprimée."""

    def setUp(self) -> None:
        from factures.repositories import FactureRepository

        self.repo = FactureRepository()

    def _make_facture(self, numero_facture: str) -> Facture:
        return Facture.objects.create(
            numero_facture=numero_facture,
            abonne_id=str(uuid.uuid4()),
            campagne_id=str(uuid.uuid4()),
            ancien_index=Decimal("100.000"),
            nouveau_index=Decimal("115.000"),
            consommation=Decimal("15.000"),
            prix_m3=Decimal("500.00"),
            montant=Decimal("7500.00"),
            date_releve=datetime.date(2025, 7, 15),
            date_limite_paiement=datetime.date(2025, 7, 20),
        )

    def test_build_numero_premiere_facture_du_mois(self) -> None:
        self.assertEqual(self.repo.build_numero(2025, 7), "FACT-2025-07-0001")

    def test_build_numero_reprend_apres_le_dernier_existant(self) -> None:
        self._make_facture("FACT-2025-07-0001")
        self._make_facture("FACT-2025-07-0002")
        self.assertEqual(self.repo.build_numero(2025, 7), "FACT-2025-07-0003")

    def test_build_numero_ignore_un_trou_dans_la_sequence(self) -> None:
        # Si une facture intermédiaire a été supprimée (0002 manquant), le
        # prochain numéro doit repartir après le plus grand existant (0005),
        # pas d'après un COUNT() qui rejouerait 0002.
        self._make_facture("FACT-2025-07-0001")
        self._make_facture("FACT-2025-07-0005")
        self.assertEqual(self.repo.build_numero(2025, 7), "FACT-2025-07-0006")

    def test_build_numero_isole_par_mois(self) -> None:
        self._make_facture("FACT-2025-06-0009")
        self.assertEqual(self.repo.build_numero(2025, 7), "FACT-2025-07-0001")
