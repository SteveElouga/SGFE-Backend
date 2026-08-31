"""Tests du reçu de paiement (montant en lettres, contexte pur, orchestration)."""

import datetime
from decimal import Decimal
from types import SimpleNamespace
from unittest.mock import MagicMock, patch

import grpc
from django.core.exceptions import ObjectDoesNotExist
from django.test import TestCase

from factures.grpc_clients import PaiementServiceClient
from factures.models import Facture, StatutFacture
from factures.pdf_generator import InfosSociete
from factures.recu_generator import (
    DonneesRecu,
    _mode_label,
    _montant_en_lettres,
    build_recu_context,
)
from factures.services import RecuPaiementService


def _recu(**kw) -> DonneesRecu:
    defaults = dict(
        numero_recu="REC-2026-06-0034",
        date_paiement="2026-06-26",
        montant=Decimal("10750"),
        mode_paiement="MOBILE_MONEY",
        reference_transaction="MM-778102934",
        enregistre_par="bah.comptable",
        montant_total=Decimal("21500"),
        total_verse=Decimal("10750"),
        nb_versements=1,
        solde_restant=Decimal("10750"),
        statut="PARTIELLE",
        numero_facture="FACT-2026-06-0002",
        facture_periode="Juin 2026",
        abonne_civilite="Mme",
        abonne_nom="Koné",
        abonne_prenom="Mariam",
        numero_abonne="AB-0002",
        quartier="Centre",
        camp="1",
        abonne_id="abcdef1234567890",
    )
    defaults.update(kw)
    return DonneesRecu(**defaults)


class MontantEnLettresTests(TestCase):
    """La conversion française est piégeuse (70/80/90, cent(s), mille) → verrou."""

    def test_cas_courants_et_limites(self):
        cases = {
            0: "zéro",
            1: "un",
            21: "vingt et un",
            70: "soixante-dix",
            71: "soixante et onze",
            80: "quatre-vingts",
            81: "quatre-vingt-un",
            90: "quatre-vingt-dix",
            91: "quatre-vingt-onze",
            100: "cent",
            200: "deux cents",
            201: "deux cent un",
            1000: "mille",
            2000: "deux mille",
            4500: "quatre mille cinq cents",
            10750: "dix mille sept cent cinquante",
            21500: "vingt et un mille cinq cents",
            80000: "quatre-vingt mille",
            100000: "cent mille",
            200000: "deux cent mille",
            206500: "deux cent six mille cinq cents",
            1000000: "un million",
        }
        for n, attendu in cases.items():
            self.assertEqual(_montant_en_lettres(n), attendu, f"pour {n}")

    def test_accepte_decimal_et_ignore_centimes(self):
        self.assertEqual(_montant_en_lettres(Decimal("10750.90")), "dix mille sept cent cinquante")


class ModeLabelTests(TestCase):
    def test_modes_connus(self):
        self.assertEqual(_mode_label("MOBILE_MONEY"), "Mobile Money")
        self.assertEqual(_mode_label("ESPECES"), "Espèces")
        self.assertEqual(_mode_label("VIREMENT"), "Virement bancaire")

    def test_mode_inconnu_est_embelli(self):
        self.assertEqual(_mode_label("PAYPAL_XYZ"), "Paypal Xyz")

    def test_mode_vide(self):
        self.assertEqual(_mode_label(""), "—")


class BuildRecuContextTests(TestCase):
    def test_mapping_versement_et_situation(self):
        ctx = build_recu_context(_recu(), InfosSociete(nom="Hydro CI", telephone="+237 690"))

        self.assertEqual(ctx["recu"]["numero"], "REC-2026-06-0034")
        self.assertEqual(ctx["recu"]["date"], "26/06/2026")
        self.assertEqual(ctx["versement"]["montant"], "10 750")
        self.assertEqual(ctx["versement"]["montant_lettres"], "Dix mille sept cent cinquante francs CFA")
        self.assertEqual(ctx["versement"]["mode"], "Mobile Money")
        self.assertTrue(ctx["versement"]["a_reference"])
        self.assertEqual(ctx["situation"]["montant_total"], "21 500 FCFA")
        self.assertEqual(ctx["situation"]["total_verse"], "10 750 FCFA")
        self.assertEqual(ctx["situation"]["solde_restant"], "10 750 FCFA")
        self.assertTrue(ctx["situation"]["solde_positif"])
        self.assertEqual(ctx["situation"]["nb_versements_label"], "1 versement")

    def test_identite_abonne_et_reference_facture(self):
        ctx = build_recu_context(_recu(), InfosSociete())
        self.assertEqual(ctx["abonne"]["nom_complet"], "Mme Koné Mariam")
        self.assertEqual(ctx["abonne"]["numero"], "AB-0002")
        self.assertEqual(ctx["abonne"]["lieu"], "Quartier Centre · Camp 1")
        self.assertEqual(ctx["enregistrement"]["par"], "bah.comptable")
        self.assertEqual(ctx["enregistrement"]["facture_ref"], "FACT-2026-06-0002 (Juin 2026)")

    def test_statut_partielle_style_bleu_et_solde_du(self):
        ctx = build_recu_context(_recu(statut="PARTIELLE"), InfosSociete())
        self.assertEqual(ctx["situation"]["statut_label"], "Facture partielle")
        self.assertEqual(ctx["situation"]["statut_texte"], "#1d4ed8")
        self.assertTrue(ctx["situation"]["solde_positif"])
        # Note « partielle » : mentionne la suspension des relances.
        self.assertIn("suspend les relances", ctx["note"])

    def test_statut_payee_style_vert_et_note_soldee(self):
        ctx = build_recu_context(
            _recu(statut="PAYEE", total_verse=Decimal("21500"), solde_restant=Decimal("0")),
            InfosSociete(),
        )
        self.assertEqual(ctx["situation"]["statut_label"], "Facture soldée")
        self.assertEqual(ctx["situation"]["statut_texte"], "#15803d")
        self.assertFalse(ctx["situation"]["solde_positif"])
        self.assertIn("soldée", ctx["note"])
        self.assertIn("FACT-2026-06-0002", ctx["note"])

    def test_pluriel_versements_et_confirmation_whatsapp(self):
        ctx = build_recu_context(_recu(nb_versements=3, whatsapp_confirme="+237 690001122"), InfosSociete())
        self.assertEqual(ctx["situation"]["nb_versements_label"], "3 versements")
        self.assertIn("confirmation WhatsApp a été envoyée au +237 690001122", ctx["note"])

    def test_repli_identite_absente(self):
        """Abonné Service inaccessible : ni nom ni numéro → repli sur l'ID technique."""
        ctx = build_recu_context(
            _recu(abonne_civilite="", abonne_nom="", abonne_prenom="", numero_abonne="", quartier="", camp=""),
            InfosSociete(),
        )
        self.assertEqual(ctx["abonne"]["nom_complet"], "Abonné abcdef12")
        self.assertEqual(ctx["abonne"]["numero"], "#abcdef12")
        self.assertEqual(ctx["abonne"]["lieu"], "")

    def test_sans_reference_transaction(self):
        ctx = build_recu_context(_recu(reference_transaction=""), InfosSociete())
        self.assertFalse(ctx["versement"]["a_reference"])
        self.assertEqual(ctx["versement"]["reference"], "—")

    def test_periode_repli_si_absente(self):
        """Sans période fournie, elle est dérivée de la date du paiement."""
        ctx = build_recu_context(_recu(facture_periode=""), InfosSociete())
        self.assertEqual(ctx["enregistrement"]["facture_ref"], "FACT-2026-06-0002 (Juin 2026)")


class RecuPaiementServiceTests(TestCase):
    """Orchestration : versement + solde (Paiement), facture (locale), identité."""

    def setUp(self):
        self.facture = Facture.objects.create(
            numero_facture="FACT-2026-06-0002",
            abonne_id="abonne-2",
            campagne_id="camp-1",
            ancien_index=Decimal("100"),
            nouveau_index=Decimal("143"),
            consommation=Decimal("43"),
            prix_m3=Decimal("500"),
            montant=Decimal("21500"),
            statut=StatutFacture.IMPAYEE,
            date_releve=datetime.date(2026, 6, 26),
            date_limite_paiement=datetime.date(2026, 7, 1),
        )

    def _service(self, paiements=None, solde=None):
        versements = (
            paiements
            if paiements is not None
            else [
                {
                    "paiement_id": "p1",
                    "facture_id": str(self.facture.id),
                    "abonne_id": "abonne-2",
                    "montant": 10750.0,
                    "date_paiement": "2026-06-26",
                    "mode_paiement": "MOBILE_MONEY",
                    "reference_transaction": "MM-778102934",
                    "created_at": "2026-06-26T11:20:00",
                    "enregistre_par": "bah.comptable",
                    "annule": False,
                }
            ]
        )
        situation = (
            solde
            if solde is not None
            else {
                "facture_id": str(self.facture.id),
                "montant_total": 21500.0,
                "montant_paye": 10750.0,
                "solde_restant": 10750.0,
                "statut": "PARTIELLE",
            }
        )
        paiement = SimpleNamespace(
            list_paiements=lambda fid, abonne_id="": versements,
            get_solde=lambda fid: situation,
        )
        abonne = SimpleNamespace(
            get_abonne=lambda aid: SimpleNamespace(
                nom="Koné", prenom="Mariam", numero_abonne="AB-0002", quartier="Centre", camp="1"
            )
        )
        config = SimpleNamespace(get_infos_societe=lambda: InfosSociete(nom="Hydro CI"))
        return RecuPaiementService(paiement_client=paiement, abonne_client=abonne, config_client=config)

    def test_genere_recu_bytes_numero_et_contexte(self):
        svc = self._service()
        with patch("factures.recu_generator.generer_recu_pdf_bytes", return_value=b"%PDF recu") as mock_gen:
            pdf, filename = svc.generer_recu_pdf("p1", str(self.facture.id))
        self.assertEqual(pdf, b"%PDF recu")
        self.assertEqual(filename, "REC-2026-06-0002-1.pdf")
        ctx = mock_gen.call_args.args[0]
        self.assertEqual(ctx["versement"]["montant"], "10 750")
        self.assertEqual(ctx["abonne"]["nom_complet"], "Koné Mariam")
        self.assertEqual(ctx["enregistrement"]["facture_ref"], "FACT-2026-06-0002 (Juin 2026)")
        self.assertEqual(ctx["situation"]["statut_label"], "Facture partielle")
        self.assertIn("11h20", ctx["enregistrement"]["date_heure"])

    def test_refuse_d_emettre_un_recu_si_le_solde_est_illisible(self):
        """Un reçu ne peut pas attester ce qu'on n'a pas pu lire.

        `get_solde` dégrade en `None` quand Paiement Service est injoignable. Le
        code écrivait `or {}` puis `... or 0`, ce qui transforme « inconnu » en
        « zéro » — et la note du reçu s'écrit à partir de ce zéro :

            « Facture soldée — ce reçu confirme le règlement intégral … »

        Un appel gRPC en échec produisait donc un DOCUMENT OFFICIEL remis à
        l'abonné, attestant un règlement intégral qui n'avait pas eu lieu, avec
        « total versé : 0 » imprimé juste au-dessus.

        L'appelant côté notification dégrade proprement : le message WhatsApp
        part sans pièce jointe. Pas de reçu vaut infiniment mieux qu'un faux.
        """
        svc = self._service()
        svc._paiement_client = SimpleNamespace(
            list_paiements=svc._paiement_client.list_paiements,
            get_solde=lambda fid: None,  # Paiement Service injoignable
        )
        with self.assertRaises(ObjectDoesNotExist):
            svc.generer_recu_pdf("p1", str(self.facture.id))

    def test_rang_du_recu_stable_parmi_plusieurs_versements(self):
        paiements = [
            {
                "paiement_id": "p1",
                "facture_id": str(self.facture.id),
                "abonne_id": "abonne-2",
                "montant": 10750.0,
                "date_paiement": "2026-06-26",
                "mode_paiement": "ESPECES",
                "reference_transaction": "",
                "created_at": "2026-06-26T09:00:00",
                "enregistre_par": "c",
                "annule": False,
            },
            {
                "paiement_id": "p2",
                "facture_id": str(self.facture.id),
                "abonne_id": "abonne-2",
                "montant": 10750.0,
                "date_paiement": "2026-06-28",
                "mode_paiement": "MOBILE_MONEY",
                "reference_transaction": "MM-2",
                "created_at": "2026-06-28T14:00:00",
                "enregistre_par": "c",
                "annule": False,
            },
        ]
        solde = {
            "facture_id": str(self.facture.id),
            "montant_total": 21500.0,
            "montant_paye": 21500.0,
            "solde_restant": 0.0,
            "statut": "PAYEE",
        }
        svc = self._service(paiements=paiements, solde=solde)
        with patch("factures.recu_generator.generer_recu_pdf_bytes", return_value=b"x") as mock_gen:
            _, filename = svc.generer_recu_pdf("p2", str(self.facture.id))
        self.assertEqual(filename, "REC-2026-06-0002-2.pdf")  # 2e versement chronologique
        ctx = mock_gen.call_args.args[0]
        self.assertEqual(ctx["situation"]["nb_versements_label"], "2 versements")
        self.assertFalse(ctx["situation"]["solde_positif"])

    def test_paiement_introuvable_leve_not_found(self):
        svc = self._service()
        with self.assertRaises(ObjectDoesNotExist):
            svc.generer_recu_pdf("inconnu", str(self.facture.id))


class PaiementClientRecuTests(TestCase):
    """Les 2 nouvelles méthodes du client Paiement (mapping proto → dict)."""

    def _client(self) -> PaiementServiceClient:
        with patch.object(grpc, "insecure_channel", return_value=MagicMock()):
            client = PaiementServiceClient()
        client._stub = MagicMock()
        return client

    def test_get_solde_mappe_les_champs(self):
        client = self._client()
        client._stub.GetSolde.return_value = SimpleNamespace(
            facture_id="f1",
            montant_total=21500.0,
            montant_paye=10750.0,
            solde_restant=10750.0,
            statut="PARTIELLE",
        )
        solde = client.get_solde("f1")
        self.assertEqual(solde["montant_paye"], 10750.0)
        self.assertEqual(solde["statut"], "PARTIELLE")

    def test_get_solde_degrade_a_none_si_erreur(self):
        client = self._client()
        client._stub.GetSolde.side_effect = Exception("indisponible")
        self.assertIsNone(client.get_solde("f1"))

    def test_list_paiements_mappe_annule_et_reference(self):
        client = self._client()
        versement = SimpleNamespace(
            paiement_id="p1",
            facture_id="f1",
            abonne_id="a1",
            montant=10750.0,
            date_paiement="2026-06-26",
            mode_paiement="MOBILE_MONEY",
            reference_transaction="MM-1",
            created_at="2026-06-26T11:20:00",
            enregistre_par="bah.comptable",
            annule=False,
        )
        client._stub.ListPaiements.return_value = SimpleNamespace(paiements=[versement])
        out = client.list_paiements("f1")
        self.assertEqual(len(out), 1)
        self.assertEqual(out[0]["mode_paiement"], "MOBILE_MONEY")
        self.assertFalse(out[0]["annule"])

    def test_list_paiements_degrade_a_vide_si_erreur(self):
        client = self._client()
        client._stub.ListPaiements.side_effect = Exception("indisponible")
        self.assertEqual(client.list_paiements("f1"), [])
