"""Tests du journal d'audit (`AuditLog`) — voir AUDIT_SGFE.md §10.7.

Vérifie que chaque mutation ciblée par l'étape 2 (génération/annulation/
régénération de facture, régularisation, tarif) écrit bien une entrée
d'audit, avec l'acteur lu depuis `get_caller()`.
"""

import datetime
import tempfile
from decimal import Decimal
from unittest.mock import patch

from django.core.exceptions import ValidationError
from django.db import transaction
from django.test import TestCase

from factures.audit import enregistrer_audit
from factures.grpc_interceptors import CallerIdentity, caller_identity
from factures.models import AuditLog, Facture
from factures.pdf_generator import InfosSociete
from factures.services import ReleveData, TarifService
from factures.tests.helpers import service_avec_clients_mockes

ABONNE = "ab-audit-1"
CAMPAGNE = "camp-audit-1"


def _facture(**kw: object) -> Facture:
    defauts = {
        "abonne_id": ABONNE,
        "campagne_id": CAMPAGNE,
        "numero_facture": kw.pop("numero", "FACT-2026-08-0099"),
        "ancien_index": Decimal("100"),
        "nouveau_index": Decimal("120"),
        "consommation": Decimal("20"),
        "prix_m3": Decimal("500"),
        "montant": Decimal("10000"),
        "date_releve": datetime.date.today(),
        "date_limite_paiement": datetime.date.today() + datetime.timedelta(days=15),
    }
    defauts.update(kw)
    return Facture.objects.create(**defauts)


class EnregistrerAuditTests(TestCase):
    def test_ecrit_l_acteur_depuis_get_caller(self) -> None:
        jeton = caller_identity.set(CallerIdentity(user_id="u-1", username="admin1", role="ADMIN"))
        try:
            enregistrer_audit(action="TEST", objet_type="Facture", objet_id="f-1", detail="détail")
        finally:
            caller_identity.reset(jeton)

        entree = AuditLog.objects.get(action="TEST")
        self.assertEqual(entree.acteur_id, "u-1")
        self.assertEqual(entree.acteur_nom, "admin1")
        self.assertEqual(entree.acteur_role, "ADMIN")

    def test_identite_vide_journalise_un_acteur_vide_sans_lever(self) -> None:
        enregistrer_audit(action="TEST_ANONYME", objet_type="Facture", objet_id="f-2")
        entree = AuditLog.objects.get(action="TEST_ANONYME")
        self.assertEqual(entree.acteur_id, "")


class TarifServiceAuditTests(TestCase):
    def setUp(self) -> None:
        self.svc = TarifService()
        jeton = caller_identity.set(CallerIdentity(user_id="u-1", username="admin1", role="ADMIN"))
        self.addCleanup(lambda: caller_identity.reset(jeton))

    def test_update_tarif_ecrit_une_entree_d_audit(self) -> None:
        tarif = self.svc.update_tarif(Decimal("500.00"), datetime.date(2025, 7, 1))
        entree = AuditLog.objects.get(action="TARIF_MODIFIE")
        self.assertEqual(entree.objet_type, "Tarif")
        self.assertEqual(entree.objet_id, str(tarif.id))
        self.assertEqual(entree.acteur_id, "u-1")


class FactureServiceAuditTests(TestCase):
    def setUp(self) -> None:
        self.svc = service_avec_clients_mockes()
        self.tarif_svc = TarifService()
        self.tarif_svc.update_tarif(Decimal("500.00"), datetime.date(2025, 7, 1))
        self.societe = InfosSociete(nom="SGFE Test", adresse="Yaoundé", telephone="+237000000000")
        AuditLog.objects.all().delete()  # ignore l'audit du tarif ci-dessus
        jeton = caller_identity.set(CallerIdentity(user_id="u-2", username="compta1", role="COMPTABLE"))
        self.addCleanup(lambda: caller_identity.reset(jeton))

    def _releve(self, abonne_id: str = ABONNE, ancien: float = 100.0, nouveau: float = 115.0) -> ReleveData:
        return ReleveData(
            abonne_id=abonne_id,
            ancien_index=ancien,
            nouveau_index=nouveau,
            consommation=nouveau - ancien,
            date_releve="2025-07-15",
        )

    def test_generer_factures_ecrit_une_entree_par_facture(self) -> None:
        releves = [self._releve("abo-1"), self._releve("abo-2", 200.0, 220.0)]
        with tempfile.TemporaryDirectory() as tmpdir:
            with patch("factures.services.settings") as mock_settings:
                mock_settings.PDF_STORAGE_DIR = tmpdir
                mock_settings.DEFAULT_DELAI_PAIEMENT_JOURS = 5
                factures = self.svc.generer_factures(
                    campagne_id=CAMPAGNE, releves=releves, delai_paiement_jours=5, societe=self.societe
                )

        entrees = AuditLog.objects.filter(action="FACTURE_GENEREE").order_by("objet_id")
        self.assertEqual(entrees.count(), 2)
        objets_id = {e.objet_id for e in entrees}
        self.assertEqual(objets_id, {str(f.id) for f in factures})
        for e in entrees:
            self.assertEqual(e.objet_type, "Facture")
            self.assertEqual(e.acteur_id, "u-2")

    def test_annuler_facture_ecrit_une_entree_d_audit(self) -> None:
        f = _facture()
        self.svc.annuler_facture(str(f.id), motif="Erreur d'index", annule_par="u-2")

        entree = AuditLog.objects.get(action="FACTURE_ANNULEE")
        self.assertEqual(entree.objet_type, "Facture")
        self.assertEqual(entree.objet_id, str(f.id))
        self.assertIn("Erreur d'index", entree.detail)
        self.assertEqual(entree.acteur_id, "u-2")

    def test_annuler_facture_deja_annulee_ne_re_ecrit_pas_d_audit(self) -> None:
        f = _facture()
        self.svc.annuler_facture(str(f.id), motif="Erreur", annule_par="u-2")
        nb_avant = AuditLog.objects.filter(action="FACTURE_ANNULEE").count()

        with self.assertRaises(ValidationError):
            self.svc.annuler_facture(str(f.id), motif="Deuxième tentative", annule_par="u-2")

        self.assertEqual(AuditLog.objects.filter(action="FACTURE_ANNULEE").count(), nb_avant)

    def test_regenerer_facture_ecrit_une_entree_pour_l_annulation_et_la_nouvelle(self) -> None:
        f = _facture()
        self.svc._campagne_client.list_releves.return_value = [  # type: ignore[attr-defined]
            {
                "abonne_id": ABONNE,
                "ancien_index": 100.0,
                "nouveau_index": 130.0,
                "consommation": 30.0,
                "date_releve": datetime.date.today().isoformat(),
                "statut": "RELEVE",
            }
        ]

        annulee, nouvelle = self.svc.regenerer_facture(
            str(f.id), motif="Index corrigé", regenere_par="u-2", delai_paiement_jours=15
        )

        self.assertTrue(AuditLog.objects.filter(action="FACTURE_ANNULEE", objet_id=str(annulee.id)).exists())
        entree_regen = AuditLog.objects.get(action="FACTURE_REGENEREE")
        self.assertEqual(entree_regen.objet_id, str(nouvelle.id))
        self.assertIn(annulee.numero_facture, entree_regen.detail)

    def test_creer_regularisation_ecrit_une_entree_d_audit(self) -> None:
        facture = self.svc.creer_regularisation(abonne_id=ABONNE, montant=15000, motif="Arriéré avant mise en service")

        entree = AuditLog.objects.get(action="REGULARISATION_CREEE")
        self.assertEqual(entree.objet_type, "Facture")
        self.assertEqual(entree.objet_id, str(facture.id))
        self.assertIn("Arriéré avant mise en service", entree.detail)
        self.assertEqual(entree.acteur_id, "u-2")


class AuditImmuabiliteEtAtomiciteTests(TestCase):
    def test_echec_dans_la_transaction_annule_l_ecriture_d_audit(self) -> None:
        with self.assertRaises(RuntimeError):
            with transaction.atomic():
                enregistrer_audit(action="TEST_ROLLBACK", objet_type="Facture", objet_id="x")
                raise RuntimeError("échec simulé après l'écriture d'audit")

        self.assertEqual(AuditLog.objects.filter(action="TEST_ROLLBACK").count(), 0)
