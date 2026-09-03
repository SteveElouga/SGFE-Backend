"""Régularisation : une dette déclarée, et sa traversée jusqu'à l'abonné.

Une régularisation est le seul montant du système que personne ne calcule.
Toutes les autres factures descendent d'un relevé : un index moins l'index
précédent, fois un prix. Celle-ci descend de la parole d'un agent. C'est ce
qui rend ses garde-fous — motif obligatoire, montant strictement positif —
structurels et non décoratifs.

La seconde moitié du fichier suit le champ ``nature`` jusqu'au protobuf. Sans
lui, l'espace abonné affiche un montant sans relevé et sans justification :
littéralement un tiret à la place de l'index et rien d'autre. Un test qui
s'arrêterait au modèle Django ne verrait pas cette régression, puisque le
modèle, lui, porte bien l'information.
"""

import datetime
from decimal import Decimal

from django.core.exceptions import ValidationError
from django.test import TestCase

from factures.models import Facture, NatureFacture, StatutFacture
from factures.serializers import facture_to_proto

from .helpers import service_avec_clients_mockes


class CreerRegularisationTest(TestCase):
    def setUp(self) -> None:
        self.svc = service_avec_clients_mockes()

    def test_cree_une_facture_de_serie_reg(self) -> None:
        f = self.svc.creer_regularisation(
            abonne_id="ab-1", montant=50_000, motif="Arriéré antérieur à la mise en service"
        )
        self.assertTrue(f.numero_facture.startswith("REG"), f.numero_facture)
        self.assertEqual(f.nature, NatureFacture.REGULARISATION)
        self.assertEqual(f.montant, Decimal("50000.00"))

    def test_sans_releve_ni_campagne(self) -> None:
        """Ni index ni campagne : c'est ce qui la distingue d'une consommation."""
        f = self.svc.creer_regularisation(abonne_id="ab-1", montant=1_000, motif="Reliquat 2025")
        self.assertEqual(f.campagne_id, "")
        self.assertEqual(f.consommation, Decimal("0"))
        self.assertEqual(f.ancien_index, Decimal("0"))
        self.assertEqual(f.nouveau_index, Decimal("0"))
        self.assertEqual(f.prix_m3, Decimal("0"))

    def test_exigible_immediatement_par_defaut(self) -> None:
        """La dette date d'avant : elle n'a pas à gagner un nouveau délai."""
        f = self.svc.creer_regularisation(abonne_id="ab-1", montant=1_000, motif="Reliquat")
        self.assertEqual(f.date_limite_paiement, datetime.date.today())

    def test_echeance_explicite_respectee(self) -> None:
        echeance = datetime.date.today() + datetime.timedelta(days=30)
        f = self.svc.creer_regularisation(
            abonne_id="ab-1",
            montant=1_000,
            motif="Étalement convenu",
            date_limite_paiement=echeance,
        )
        self.assertEqual(f.date_limite_paiement, echeance)

    def test_motif_vide_refuse(self) -> None:
        for motif in ("", "   "):
            with self.subTest(motif=repr(motif)):
                with self.assertRaises(ValidationError):
                    self.svc.creer_regularisation(abonne_id="ab-1", montant=1_000, motif=motif)

    def test_montant_nul_ou_negatif_refuse(self) -> None:
        for montant in (0, -1, -50_000):
            with self.subTest(montant=montant):
                with self.assertRaises(ValidationError):
                    self.svc.creer_regularisation(abonne_id="ab-1", montant=montant, motif="X")

    def test_abonne_manquant_refuse(self) -> None:
        with self.assertRaises(ValidationError):
            self.svc.creer_regularisation(abonne_id="", montant=1_000, motif="X")

    def test_motif_est_deponctue_des_espaces(self) -> None:
        f = self.svc.creer_regularisation(abonne_id="ab-1", montant=1_000, motif="  Reliquat  ")
        self.assertEqual(f.motif, "Reliquat")

    def test_un_solde_est_initialise(self) -> None:
        """Sans solde, la dette n'entre ni dans les impayés ni dans les relances."""
        f = self.svc.creer_regularisation(abonne_id="ab-1", montant=7_500, motif="Reliquat")
        appel = self.svc._paiement_client.initialiser_solde.call_args.kwargs  # type: ignore[attr-defined]
        self.assertEqual(appel["facture_id"], str(f.id))
        self.assertEqual(appel["montant_total"], 7500.0)

    def test_deux_regularisations_ne_se_marchent_pas_dessus(self) -> None:
        a = self.svc.creer_regularisation(abonne_id="ab-1", montant=1_000, motif="A")
        b = self.svc.creer_regularisation(abonne_id="ab-1", montant=2_000, motif="B")
        self.assertNotEqual(a.numero_facture, b.numero_facture)
        self.assertEqual(Facture.objects.filter(abonne_id="ab-1").count(), 2)

    def test_elle_est_impayee_a_la_naissance(self) -> None:
        f = self.svc.creer_regularisation(abonne_id="ab-1", montant=1_000, motif="Reliquat")
        self.assertEqual(f.statut, StatutFacture.IMPAYEE)


class NatureDansLeProtoTest(TestCase):
    """Le champ doit survivre au passage en protobuf, pas seulement en base.

    L'espace abonné et les relances lisent le proto, jamais le modèle : c'est
    la frontière où l'information se perdait.
    """

    def setUp(self) -> None:
        self.svc = service_avec_clients_mockes()

    def test_regularisation_transporte_sa_nature_et_son_motif(self) -> None:
        f = self.svc.creer_regularisation(
            abonne_id="ab-1", montant=50_000, motif="Arriéré antérieur à la mise en service"
        )
        proto = facture_to_proto(f)
        self.assertEqual(proto.nature, "REGULARISATION")
        self.assertEqual(proto.motif, "Arriéré antérieur à la mise en service")

    def test_une_consommation_se_declare_aussi(self) -> None:
        """Le défaut ne doit pas être la chaîne vide : le client teste la valeur."""
        f = Facture.objects.create(
            abonne_id="ab-2",
            campagne_id="camp-1",
            numero_facture="FACT-2026-08-0001",
            ancien_index=Decimal("100"),
            nouveau_index=Decimal("112"),
            consommation=Decimal("12"),
            prix_m3=Decimal("500"),
            montant=Decimal("6000"),
            date_releve=datetime.date.today(),
            date_limite_paiement=datetime.date.today() + datetime.timedelta(days=15),
        )
        proto = facture_to_proto(f)
        self.assertEqual(proto.nature, "CONSOMMATION")
        self.assertEqual(proto.motif, "")
