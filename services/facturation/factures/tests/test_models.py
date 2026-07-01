"""Tests des modèles Tarif et Facture."""

import datetime
import uuid
from decimal import Decimal

from django.test import TestCase

from factures.models import Facture, StatutFacture, Tarif


class TarifModelTests(TestCase):
    def test_creation_tarif(self):
        tarif = Tarif.objects.create(
            prix_m3=Decimal("500.00"),
            date_effet=datetime.date(2025, 7, 1),
            is_active=True,
        )
        self.assertIsNotNone(tarif.id)
        self.assertTrue(tarif.is_active)
        self.assertEqual(tarif.prix_m3, Decimal("500.00"))

    def test_str_tarif(self):
        tarif = Tarif(
            prix_m3=Decimal("500.00"),
            date_effet=datetime.date(2025, 7, 1),
            is_active=True,
        )
        self.assertIn("500", str(tarif))
        self.assertIn("actif", str(tarif))


class FactureModelTests(TestCase):
    def _make_facture(self, **kwargs) -> Facture:
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

    def test_creation_facture(self):
        facture = self._make_facture()
        self.assertIsNotNone(facture.id)
        self.assertEqual(facture.statut, StatutFacture.IMPAYEE)
        self.assertEqual(facture.montant, Decimal("7500.00"))

    def test_str_facture(self):
        facture = self._make_facture(numero_facture="FACT-2025-07-0001")
        self.assertIn("FACT-2025-07-0001", str(facture))

    def test_numero_facture_unique(self):
        from django.db import IntegrityError

        self._make_facture(numero_facture="FACT-2025-07-0001")
        with self.assertRaises(IntegrityError):
            self._make_facture(numero_facture="FACT-2025-07-0001")
