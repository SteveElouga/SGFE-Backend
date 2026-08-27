"""Annuler une facture, et en émettre une corrigée.

Une facture annulée n'est pas supprimée. Elle reste au journal avec son numéro,
son motif et le nom de qui l'a annulée : une numérotation comptable dont des
numéros disparaissent n'est plus une numérotation, et le trou est précisément
ce qui prouve qu'on a effacé quelque chose.

La régénération relit le relevé au lieu de recopier les index de la facture
annulée. C'est tout l'intérêt du geste : corriger un index puis régénérer doit
produire la facture juste, pas reproduire fidèlement l'erreur qu'on répare.
"""

import datetime
from decimal import Decimal

from django.core.exceptions import ValidationError
from django.test import TestCase

from factures.exceptions import PreconditionError
from factures.models import Facture, NatureFacture, StatutFacture, Tarif

from .helpers import service_avec_clients_mockes

ABONNE = "ab-annul-1"
CAMPAGNE = "camp-1"


def _facture(svc, **kw) -> Facture:
    defauts = dict(
        abonne_id=ABONNE,
        campagne_id=CAMPAGNE,
        numero_facture=kw.pop("numero", "FACT-2026-08-0001"),
        ancien_index=Decimal("100"),
        nouveau_index=Decimal("120"),
        consommation=Decimal("20"),
        prix_m3=Decimal("500"),
        montant=Decimal("10000"),
        date_releve=datetime.date.today(),
        date_limite_paiement=datetime.date.today() + datetime.timedelta(days=15),
    )
    defauts.update(kw)
    return Facture.objects.create(**defauts)


class AnnulerFactureTest(TestCase):
    def setUp(self):
        self.svc = service_avec_clients_mockes()

    def test_la_facture_reste_au_journal_avec_son_numero(self):
        f = _facture(self.svc)
        annulee = self.svc.annuler_facture(str(f.id), motif="Erreur d'index", annule_par="admin")
        self.assertEqual(annulee.statut, StatutFacture.ANNULEE)
        self.assertEqual(annulee.numero_facture, "FACT-2026-08-0001")
        self.assertTrue(Facture.objects.filter(id=f.id).exists())

    def test_le_motif_et_l_auteur_sont_conserves(self):
        f = _facture(self.svc)
        annulee = self.svc.annuler_facture(str(f.id), motif="Index relevé sur le mauvais compteur", annule_par="claire")
        self.assertEqual(annulee.motif_annulation, "Index relevé sur le mauvais compteur")
        self.assertEqual(annulee.annulee_par, "claire")
        self.assertIsNotNone(annulee.date_annulation)

    def test_le_motif_vide_est_refuse(self):
        f = _facture(self.svc)
        for motif in ("", "   "):
            with self.subTest(motif=repr(motif)):
                with self.assertRaises(ValidationError):
                    self.svc.annuler_facture(str(f.id), motif=motif, annule_par="admin")

    def test_le_solde_est_eteint_avant_le_marquage(self):
        """Une facture annulée dont la dette court encore serait pire que rien."""
        f = _facture(self.svc)
        self.svc.annuler_facture(str(f.id), motif="Erreur", annule_par="admin")
        self.svc._paiement_client.annuler_solde.assert_called_once()
        appel = self.svc._paiement_client.annuler_solde.call_args.kwargs
        self.assertEqual(appel["facture_id"], str(f.id))

    def test_un_service_paiement_indisponible_empeche_l_annulation(self):
        f = _facture(self.svc)
        self.svc._paiement_client.annuler_solde.side_effect = RuntimeError("paiement KO")
        with self.assertRaises(RuntimeError):
            self.svc.annuler_facture(str(f.id), motif="Erreur", annule_par="admin")
        f.refresh_from_db()
        self.assertEqual(f.statut, StatutFacture.IMPAYEE)

    def test_annuler_deux_fois_est_refuse(self):
        f = _facture(self.svc)
        self.svc.annuler_facture(str(f.id), motif="Erreur", annule_par="admin")
        with self.assertRaises(ValidationError):
            self.svc.annuler_facture(str(f.id), motif="Encore", annule_par="admin")

    def test_une_regularisation_s_annule_aussi(self):
        """Une dette saisie à la main peut avoir été saisie à tort."""
        f = _facture(self.svc, nature=NatureFacture.REGULARISATION, campagne_id="", numero="REG-2026-08-0001")
        annulee = self.svc.annuler_facture(str(f.id), motif="Saisie en double", annule_par="admin")
        self.assertEqual(annulee.statut, StatutFacture.ANNULEE)


class RegenererFactureTest(TestCase):
    def setUp(self):
        self.svc = service_avec_clients_mockes()
        Tarif.objects.create(prix_m3=Decimal("500"), date_effet=datetime.date.today(), is_active=True)

    def _relevé(self, ancien="100", nouveau="130"):
        self.svc._campagne_client.list_releves.return_value = [
            {
                "abonne_id": ABONNE,
                "ancien_index": float(ancien),
                "nouveau_index": float(nouveau),
                "consommation": float(Decimal(nouveau) - Decimal(ancien)),
                "date_releve": datetime.date.today().isoformat(),
                "statut": "RELEVE",
            }
        ]

    def test_la_nouvelle_facture_suit_le_releve_corrige_pas_l_ancienne(self):
        """C'est tout l'intérêt du geste : recopier reproduirait l'erreur."""
        f = _facture(self.svc)  # 100 → 120, 10 000 FCFA
        self._relevé(ancien="100", nouveau="130")  # l'index a été corrigé depuis

        annulee, nouvelle = self.svc.regenerer_facture(
            str(f.id), motif="Index corrigé", regenere_par="admin", delai_paiement_jours=15
        )

        self.assertEqual(annulee.statut, StatutFacture.ANNULEE)
        self.assertEqual(nouvelle.nouveau_index, Decimal("130.000"))
        self.assertEqual(nouvelle.consommation, Decimal("30.000"))
        self.assertEqual(nouvelle.montant, Decimal("15000.00"))

    def test_les_deux_factures_se_citent(self):
        f = _facture(self.svc)
        self._relevé()
        annulee, nouvelle = self.svc.regenerer_facture(
            str(f.id), motif="Correction", regenere_par="admin", delai_paiement_jours=15
        )
        self.assertEqual(annulee.remplacee_par_id, str(nouvelle.id))
        self.assertEqual(nouvelle.remplace_id, str(annulee.id))

    def test_la_nouvelle_porte_un_numero_different(self):
        f = _facture(self.svc)
        self._relevé()
        annulee, nouvelle = self.svc.regenerer_facture(
            str(f.id), motif="Correction", regenere_par="admin", delai_paiement_jours=15
        )
        self.assertNotEqual(nouvelle.numero_facture, annulee.numero_facture)

    def test_un_solde_est_ouvert_pour_la_nouvelle(self):
        """C'est lui qui récupérera l'avoir né de l'annulation."""
        f = _facture(self.svc)
        self._relevé()
        _a, nouvelle = self.svc.regenerer_facture(
            str(f.id), motif="Correction", regenere_par="admin", delai_paiement_jours=15
        )
        appel = self.svc._paiement_client.initialiser_solde.call_args.kwargs
        self.assertEqual(appel["facture_id"], str(nouvelle.id))
        self.assertEqual(appel["montant_total"], float(nouvelle.montant))

    def test_une_regularisation_ne_se_regenere_pas(self):
        """Son montant est déclaré, pas calculé : il n'y a rien à recalculer."""
        f = _facture(self.svc, nature=NatureFacture.REGULARISATION, campagne_id="", numero="REG-2026-08-0002")
        with self.assertRaises(ValidationError):
            self.svc.regenerer_facture(str(f.id), motif="Correction", regenere_par="admin", delai_paiement_jours=15)

    def test_sans_releve_la_regeneration_est_refusee(self):
        f = _facture(self.svc)
        self.svc._campagne_client.list_releves.return_value = []
        with self.assertRaises(PreconditionError):
            self.svc.regenerer_facture(str(f.id), motif="Correction", regenere_par="admin", delai_paiement_jours=15)
        f.refresh_from_db()
        self.assertEqual(f.statut, StatutFacture.IMPAYEE, "l'ancienne ne doit pas être annulée pour rien")

    def test_un_releve_incoherent_est_refuse_avant_d_annuler(self):
        f = _facture(self.svc)
        self._relevé(ancien="130", nouveau="100")
        with self.assertRaises(ValidationError):
            self.svc.regenerer_facture(str(f.id), motif="Correction", regenere_par="admin", delai_paiement_jours=15)
        f.refresh_from_db()
        self.assertEqual(f.statut, StatutFacture.IMPAYEE)

    def test_campagne_service_indisponible_n_annule_rien(self):
        f = _facture(self.svc)
        self.svc._campagne_client.list_releves.side_effect = RuntimeError("campagne KO")
        with self.assertRaises(PreconditionError):
            self.svc.regenerer_facture(str(f.id), motif="Correction", regenere_par="admin", delai_paiement_jours=15)
        f.refresh_from_db()
        self.assertEqual(f.statut, StatutFacture.IMPAYEE)
