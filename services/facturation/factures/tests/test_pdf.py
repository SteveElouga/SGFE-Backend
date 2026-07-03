"""Tests du gabarit HTML de facture et du client Abonné.

Le rendu PDF réel (WeasyPrint) dépend de bibliothèques natives (pango/cairo)
absentes de l'environnement de CI ; il est vérifié séparément par un smoke-test
Docker. Ici on teste `_build_html` (Python pur) et la logique du client gRPC.
"""

from decimal import Decimal
from unittest.mock import MagicMock

from django.test import SimpleTestCase

from factures.pdf_generator import (
    DonneesFacture,
    InfosSociete,
    _build_html,
    _date_fr,
    _fcfa,
    _num,
    _periode_fr,
)


def _donnees(**kwargs) -> DonneesFacture:
    defaults = dict(
        numero_facture="FACT-2026-06-0002",
        abonne_id="abcdef12-3456-7890-aaaa-bbbbbbbbbbbb",
        campagne_id="camp-001",
        ancien_index=Decimal("1820.000"),
        nouveau_index=Decimal("1863.000"),
        consommation=Decimal("43.000"),
        prix_m3=Decimal("500.00"),
        montant=Decimal("21500.00"),
        statut="IMPAYEE",
        date_releve="2026-06-15",
        date_limite_paiement="2026-06-20",
        date_generation="15/06/2026 10:34",
    )
    defaults.update(kwargs)
    return DonneesFacture(**defaults)


class HelpersTests(SimpleTestCase):
    def test_fcfa_entier(self):
        self.assertEqual(_fcfa(Decimal("21500.00")), "21 500 FCFA")

    def test_fcfa_avec_decimales(self):
        self.assertEqual(_fcfa(Decimal("1234.50")), "1 234,50 FCFA")

    def test_num_entier_groupe(self):
        self.assertEqual(_num(Decimal("1863.000")), "1 863")

    def test_date_fr(self):
        self.assertEqual(_date_fr("2026-06-20"), "20/06/2026")

    def test_date_fr_invalide_repli(self):
        self.assertEqual(_date_fr("pas-une-date"), "pas-une-date")

    def test_periode_fr(self):
        self.assertEqual(_periode_fr("2026-06-15"), "Juin 2026")


class BuildHtmlTests(SimpleTestCase):
    def setUp(self):
        self.societe = InfosSociete(
            nom="Hydro Services CI",
            adresse="Quartier Centre, Camp 1 — Yamoussoukro",
            telephone="+225 07 00 11 22 33",
        )

    def test_identite_complete_affichee(self):
        donnees = _donnees(
            numero_abonne="AB-0002",
            abonne_nom="Koné",
            abonne_prenom="Mariam",
            abonne_whatsapp="+225 07 33 44 55 66",
            numero_compteur="0387",
            quartier="Centre",
            camp="1",
            numero_mobile_money="0700112233",
        )
        html = _build_html(donnees, self.societe)

        self.assertIn("Hydro Services CI", html)
        self.assertIn("FACT-2026-06-0002", html)
        self.assertIn("Koné Mariam", html)
        self.assertIn("AB-0002", html)
        self.assertIn("N° 0387", html)
        self.assertIn("Quartier Centre", html)
        self.assertIn("Camp 1", html)
        self.assertIn("+225 07 33 44 55 66", html)
        self.assertIn("Juin 2026", html)
        self.assertIn("1 820", html)  # ancien index groupé
        self.assertIn("1 863", html)  # nouvel index groupé
        self.assertIn("21 500 FCFA", html)  # montant
        self.assertIn("20/06/2026", html)  # date limite
        self.assertIn("0700112233", html)  # mobile money

    def test_sans_identite_repli(self):
        """Abonné Service inaccessible : le PDF reste généré avec des replis."""
        donnees = _donnees()  # aucun champ d'identité
        html = _build_html(donnees, self.societe)

        self.assertIn("Abonné abcdef12", html)  # nom de repli (id tronqué)
        self.assertIn("N° —", html)  # compteur inconnu
        self.assertNotIn("WhatsApp :", html)  # ligne omise si absente
        self.assertNotIn("Mobile Money au", html)  # omise si pas de numéro

    def test_echappement_html(self):
        """Les données abonné sont échappées (pas d'injection dans le gabarit)."""
        donnees = _donnees(abonne_nom="<script>alert(1)</script>", abonne_prenom="")
        html = _build_html(donnees, self.societe)

        self.assertNotIn("<script>alert(1)</script>", html)
        self.assertIn("&lt;script&gt;", html)


class AbonneServiceClientTests(SimpleTestCase):
    def _client(self):
        from factures.grpc_clients import AbonneServiceClient

        client = AbonneServiceClient()
        client._stub = MagicMock()
        client._pb = MagicMock()
        return client

    def test_get_abonne_mappe_les_champs(self):
        client = self._client()
        compteur = MagicMock(numero_compteur=387, quartier="Centre", camp=1)
        client._stub.GetAbonne.return_value = MagicMock(
            numero_abonne="AB-0002",
            nom="Koné",
            prenom="Mariam",
            telephone_whatsapp="+225070000",
            adresse="",
            compteur=compteur,
        )
        identite = client.get_abonne("abo-001")
        self.assertEqual(identite.numero_abonne, "AB-0002")
        self.assertEqual(identite.nom, "Koné")
        self.assertEqual(identite.numero_compteur, "0387")  # zéro-padding sur 4 chiffres
        self.assertEqual(identite.quartier, "Centre")
        self.assertEqual(identite.camp, "1")

    def test_get_abonne_erreur_retourne_none(self):
        client = self._client()
        client._stub.GetAbonne.side_effect = RuntimeError("service KO")
        self.assertIsNone(client.get_abonne("abo-001"))


class GenererPdfReelTests(SimpleTestCase):
    """Rendu PDF réel — exécuté uniquement là où WeasyPrint est disponible."""

    def test_generer_pdf_produit_un_pdf(self):
        try:
            import weasyprint  # noqa: F401
        except Exception:
            self.skipTest("WeasyPrint natif indisponible (vérifié en Docker)")

        import tempfile

        from factures.pdf_generator import generer_pdf

        with tempfile.TemporaryDirectory() as tmpdir:
            path = generer_pdf(_donnees(abonne_nom="Koné", abonne_prenom="Mariam"), self._societe(), tmpdir)
            with open(path, "rb") as f:
                head = f.read(5)
        self.assertEqual(head, b"%PDF-")

    @staticmethod
    def _societe() -> InfosSociete:
        return InfosSociete(nom="Hydro Services CI")
