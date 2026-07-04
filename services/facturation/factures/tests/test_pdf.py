"""Tests du gabarit HTML de facture et du client Abonné/Campagne.

Le rendu PDF réel (WeasyPrint) dépend de bibliothèques natives (pango/cairo)
absentes de certains environnements de CI ; il est vérifié séparément par un
smoke-test Docker. Ici on teste `_build_context`/`build_historique` (Python
pur), le rendu du gabarit Django (`render_to_string`, sans WeasyPrint) et la
logique des clients gRPC.
"""

from decimal import Decimal
from unittest.mock import MagicMock

from django.template.loader import render_to_string
from django.test import SimpleTestCase

from factures.pdf_generator import (
    DonneesFacture,
    InfosSociete,
    _build_context,
    _date_fr,
    _fcfa,
    _modalite_delai,
    _nom_abonne,
    _num,
    _periode_fr,
    build_historique,
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

    def test_modalite_delai_cinq_jours(self):
        self.assertEqual(_modalite_delai("2026-06-15", "2026-06-20"), "sous 5 jours")

    def test_modalite_delai_reflete_le_delai_configure(self):
        # Délai porté à 10 jours (config) → la phrase suit, jamais figée à 5.
        self.assertEqual(_modalite_delai("2026-06-15", "2026-06-25"), "sous 10 jours")

    def test_modalite_delai_singulier(self):
        self.assertEqual(_modalite_delai("2026-06-15", "2026-06-16"), "sous 1 jour")

    def test_modalite_delai_dates_illisibles_repli(self):
        self.assertEqual(_modalite_delai("pas-une-date", "2026-06-20"), "dans les meilleurs délais")

    def test_modalite_delai_ecart_nul_repli(self):
        self.assertEqual(_modalite_delai("2026-06-20", "2026-06-20"), "dans les meilleurs délais")

    def test_nom_abonne_complet(self):
        donnees = _donnees(abonne_nom="Koné", abonne_prenom="Mariam")
        self.assertEqual(_nom_abonne(donnees), ("Koné", "Mariam"))

    def test_nom_abonne_repli_sur_identifiant(self):
        donnees = _donnees()  # aucun champ d'identité
        nom, prenom = _nom_abonne(donnees)
        self.assertEqual(nom, "Abonné abcdef12")
        self.assertEqual(prenom, "")


class BuildHistoriqueTests(SimpleTestCase):
    def test_vide_retourne_liste_vide(self):
        self.assertEqual(build_historique([]), [])

    def test_hauteur_proportionnelle_au_maximum(self):
        entries = [
            ("2026-04-15", Decimal("20"), False),
            ("2026-05-15", Decimal("40"), False),
            ("2026-06-15", Decimal("10"), True),
        ]
        points = build_historique(entries)
        self.assertEqual([p.hauteur_px for p in points], [22, 44, 11])
        self.assertEqual([p.label for p in points], ["Avr", "Mai", "Juin"])
        self.assertEqual([p.is_actuel for p in points], [False, False, True])
        self.assertEqual([p.conso for p in points], ["20", "40", "10"])

    def test_hauteur_bornee_au_minimum(self):
        entries = [("2026-06-15", Decimal("0"), True), ("2026-05-15", Decimal("100"), False)]
        points = build_historique(entries)
        # Le point à 0 doit rester visible (borne basse) plutôt que disparaître.
        self.assertEqual(points[0].hauteur_px, 4)

    def test_toutes_conso_nulles_ne_divise_pas_par_zero(self):
        entries = [("2026-06-15", Decimal("0"), True), ("2026-05-15", Decimal("0"), False)]
        points = build_historique(entries)
        self.assertTrue(all(p.hauteur_px == 4 for p in points))

    def test_date_invalide_label_vide(self):
        points = build_historique([("pas-une-date", Decimal("10"), True)])
        self.assertEqual(points[0].label, "")


class BuildContextTests(SimpleTestCase):
    def setUp(self):
        self.societe = InfosSociete(
            nom="Hydro Services CI",
            adresse="Quartier Centre, Camp 1 — Yamoussoukro",
            telephone="+225 07 00 11 22 33",
        )

    def test_contexte_identite_complete(self):
        donnees = _donnees(
            numero_abonne="AB-0002",
            abonne_nom="Koné",
            abonne_prenom="Mariam",
            abonne_whatsapp="+225 07 33 44 55 66",
            numero_compteur="0387",
            quartier="Centre",
            camp="1",
            numero_mobile_money="0700112233",
            campagne_nom="Campagne Juin 2026",
        )
        ctx = _build_context(donnees, self.societe)

        self.assertEqual(ctx["societe"]["nom"], "Hydro Services CI")
        self.assertEqual(ctx["societe"]["adresse_ligne1"], "Quartier Centre, Camp 1 — Yamoussoukro")
        self.assertEqual(ctx["facture"]["numero"], "FACT-2026-06-0002")
        self.assertEqual(ctx["facture"]["ancien_index"], "1 820")
        self.assertEqual(ctx["facture"]["nouveau_index"], "1 863")
        self.assertEqual(ctx["facture"]["montant"], "21 500 FCFA")
        self.assertEqual(ctx["facture"]["sous_total"], "21 500 FCFA")
        self.assertEqual(ctx["facture"]["total"], "21 500 FCFA")
        self.assertEqual(ctx["facture"]["frais_supplementaires"], "0 FCFA")
        self.assertEqual(ctx["facture"]["date_limite_paiement"], "20/06/2026")
        self.assertEqual(ctx["abonne"]["nom"], "Koné")
        self.assertEqual(ctx["abonne"]["prenom"], "Mariam")
        self.assertEqual(ctx["abonne"]["numero"], "AB-0002")
        self.assertEqual(ctx["abonne"]["quartier"], "Quartier Centre")
        self.assertEqual(ctx["abonne"]["camp"], "1")
        self.assertEqual(ctx["compteur"]["numero"], "0387")
        self.assertEqual(ctx["releve"]["campagne_nom"], "Campagne Juin 2026")
        self.assertEqual(ctx["espace"]["url"], "")

    def test_contexte_sans_identite_repli(self):
        donnees = _donnees()  # aucun champ d'identité, ni campagne_nom
        ctx = _build_context(donnees, self.societe)

        self.assertEqual(ctx["abonne"]["nom"], "Abonné abcdef12")
        self.assertEqual(ctx["compteur"]["numero"], "—")
        # Repli sur la période du relevé si le nom de campagne est indisponible.
        self.assertEqual(ctx["releve"]["campagne_nom"], "Juin 2026")

    def test_contexte_espace_url_et_date_formatee(self):
        """L'URL espace est transmise telle quelle ; la date ISO est formatée JJ/MM/AAAA."""
        donnees = _donnees(espace_url="aquabill.ci/espace/abc", espace_date_expiration="2026-07-05")
        ctx = _build_context(donnees, self.societe)
        self.assertEqual(ctx["espace"]["url"], "aquabill.ci/espace/abc")
        self.assertEqual(ctx["espace"]["date_expiration"], "05/07/2026")

    def test_contexte_espace_vide_par_defaut(self):
        """Sans URL espace, le bloc reste vide (masqué dans le gabarit)."""
        ctx = _build_context(_donnees(), self.societe)
        self.assertEqual(ctx["espace"]["url"], "")
        self.assertEqual(ctx["espace"]["date_expiration"], "")


class RenderTemplateTests(SimpleTestCase):
    """Rendu du gabarit Django (sans WeasyPrint) — valide la syntaxe et l'échappement."""

    def setUp(self):
        self.societe = InfosSociete(nom="Hydro Services CI", telephone="+225 07 00 11 22 33")

    def test_rendu_contient_les_donnees_attendues(self):
        donnees = _donnees(abonne_nom="Koné", abonne_prenom="Mariam", numero_abonne="AB-0002")
        historique = build_historique([("2026-05-15", Decimal("30"), False), ("2026-06-15", Decimal("43"), True)])
        html = render_to_string("facture_pdf.html", _build_context(donnees, self.societe, historique))

        self.assertIn("Hydro Services CI", html)
        self.assertIn("FACT-2026-06-0002", html)
        self.assertIn("Koné", html)
        self.assertIn("Mariam", html)
        self.assertIn("AB-0002", html)
        self.assertIn("21 500 FCFA", html)
        self.assertIn("Juin", html)  # étiquette du mois courant dans l'histogramme

    def test_commentaire_entete_non_visible_dans_le_rendu(self):
        """Régression : {# #} de Django ne supporte pas les commentaires multi-lignes

        (contrairement à {% comment %}) — un {# #} multi-ligne n'est pas
        supprimé et son contenu apparaît tel quel dans le PDF généré.
        """
        html = render_to_string("facture_pdf.html", _build_context(_donnees(), self.societe))
        self.assertTrue(html.lstrip().startswith("<!DOCTYPE html>"))
        self.assertNotIn("TEMPLATE FACTURE PDF", html)
        self.assertNotIn("CONTRAT DE CONTEXTE", html)

    def test_barres_histogramme_largeur_fixe(self):
        """Régression : `width: 100%` sur un enfant flex non-stretch (align-items:

        center) est mal résolu par WeasyPrint (barres qui débordent de leur
        colonne et se chevauchent) — la largeur doit rester fixe en pixels.
        """
        html = render_to_string("facture_pdf.html", _build_context(_donnees(), self.societe))
        style = html.split("<style>")[1].split("</style>")[0]
        bar_rule = [line for line in style.splitlines() if ".hist__bar {" in line][0]
        self.assertIn("width: 28px", bar_rule)
        self.assertNotIn("width: 100%", bar_rule)

    def test_barres_histogramme_marge_haut_calculee(self):
        """Chaque barre porte son propre `margin-top` (hauteur constante de colonne)."""
        historique = build_historique([("2026-05-15", Decimal("10"), False), ("2026-06-15", Decimal("40"), True)])
        html = render_to_string("facture_pdf.html", _build_context(_donnees(), self.societe, historique))
        for mois in historique:
            self.assertIn(f"margin-top: {mois.marge_haut_px}px", html)

    def test_bloc_espace_masque_si_aucun_token(self):
        donnees = _donnees()  # espace_url vide par défaut
        html = render_to_string("facture_pdf.html", _build_context(donnees, self.societe))
        self.assertNotIn("Consultez votre historique en ligne", html)

    def test_bloc_espace_affiche_si_token_fourni(self):
        donnees = _donnees(espace_url="https://exemple.test/espace/abc123", espace_date_expiration="05/07/2026")
        html = render_to_string("facture_pdf.html", _build_context(donnees, self.societe))
        self.assertIn("https://exemple.test/espace/abc123", html)
        self.assertIn("05/07/2026", html)

    def test_modalite_delai_dynamique_dans_le_rendu(self):
        """Le délai de règlement suit les dates de la facture — pas de « 5 jours » codé en dur."""
        donnees = _donnees(date_releve="2026-06-15", date_limite_paiement="2026-06-25")  # 10 jours
        html = render_to_string("facture_pdf.html", _build_context(donnees, self.societe))
        self.assertIn("Règlement sous 10 jours", html)
        self.assertNotIn("sous 5 jours", html)

    def test_echappement_html_par_django(self):
        """Django auto-échappe les variables — pas d'injection possible dans le gabarit."""
        donnees = _donnees(abonne_nom="<script>alert(1)</script>", abonne_prenom="")
        html = render_to_string("facture_pdf.html", _build_context(donnees, self.societe))
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


class CampagneServiceClientTests(SimpleTestCase):
    def _client(self):
        from factures.grpc_clients import CampagneServiceClient

        client = CampagneServiceClient()
        client._stub = MagicMock()
        client._pb = MagicMock()
        return client

    def test_get_campagne_nom_retourne_le_nom(self):
        client = self._client()
        client._stub.GetCampagne.return_value = MagicMock(nom="Campagne Juin 2026")
        self.assertEqual(client.get_campagne_nom("camp-001"), "Campagne Juin 2026")

    def test_get_campagne_nom_erreur_retourne_chaine_vide(self):
        import grpc

        client = self._client()
        client._stub.GetCampagne.side_effect = grpc.RpcError("service KO")
        self.assertEqual(client.get_campagne_nom("camp-001"), "")


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
