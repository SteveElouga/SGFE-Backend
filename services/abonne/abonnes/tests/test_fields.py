"""Tests du chiffrement au repos des PII abonné (abonnes/fields.py)."""

from django.core.exceptions import FieldError, ImproperlyConfigured
from django.db import connection
from django.test import TestCase, override_settings

from abonnes import fields
from abonnes.models import Abonne


class EncryptedFieldTransparencyTests(TestCase):
    """Le chiffrement doit être invisible pour le code applicatif : on écrit
    et on lit des chaînes en clair, comme avant."""

    def test_round_trip_plain_text_via_orm(self):
        Abonne.objects.create(
            numero_abonne="AB-1001",
            nom="Mbarga",
            prenom="Alice",
            telephone_whatsapp="+237690000010",
            adresse="Quartier Nkolbisson",
        )
        abonne = Abonne.objects.get(numero_abonne="AB-1001")
        self.assertEqual(abonne.nom, "Mbarga")
        self.assertEqual(abonne.prenom, "Alice")
        self.assertEqual(abonne.telephone_whatsapp, "+237690000010")
        self.assertEqual(abonne.adresse, "Quartier Nkolbisson")

    def test_adresse_vide_reste_chaine_vide(self):
        """`adresse` a `default=""` — un token Fernet pour une chaîne vide
        serait absurde (et le champ ne serait plus jamais "vide" au sens
        applicatif) : le champ chiffré doit laisser passer '' tel quel."""
        abonne = Abonne.objects.create(numero_abonne="AB-1002", nom="X", prenom="Y", telephone_whatsapp="+237690000011")
        abonne.refresh_from_db()
        self.assertEqual(abonne.adresse, "")

    def test_valeur_stockee_en_base_est_bien_chiffree(self):
        """Vérifie directement la colonne en base (hors ORM, donc hors
        déchiffrement automatique) : le texte en clair ne doit PAS y
        apparaître tel quel."""
        Abonne.objects.create(
            numero_abonne="AB-1003",
            nom="Nom secret unique 42",
            prenom="Prenom",
            telephone_whatsapp="+237690000012",
            adresse="Adresse secrète unique 42",
        )
        with connection.cursor() as cursor:
            cursor.execute("SELECT nom, adresse FROM abonnes WHERE numero_abonne = %s", ["AB-1003"])
            raw_nom, raw_adresse = cursor.fetchone()
        self.assertNotEqual(raw_nom, "Nom secret unique 42")
        self.assertNotIn("secret", raw_nom)
        self.assertNotEqual(raw_adresse, "Adresse secrète unique 42")
        self.assertNotIn("secrète", raw_adresse)

    def test_deux_chiffrements_de_la_meme_valeur_different(self):
        """Fernet est non déterministe (IV + horodatage aléatoires) — deux
        lignes avec le même prénom ne doivent pas produire le même texte
        chiffré (sinon on pourrait corréler des abonnés par simple égalité
        de colonne chiffrée, ce qui romprait la confidentialité)."""
        Abonne.objects.create(numero_abonne="AB-1004", nom="Doe", prenom="Homonyme", telephone_whatsapp="+237690000013")
        Abonne.objects.create(numero_abonne="AB-1005", nom="Doe", prenom="Homonyme", telephone_whatsapp="+237690000014")
        with connection.cursor() as cursor:
            cursor.execute("SELECT prenom FROM abonnes WHERE numero_abonne IN (%s, %s)", ["AB-1004", "AB-1005"])
            values = {row[0] for row in cursor.fetchall()}
        self.assertEqual(len(values), 2)


class EncryptedFieldLookupTests(TestCase):
    """Un contenu chiffré non déterministe ne peut pas être filtré en base —
    voir la doc de abonnes/fields.py. Ces filtres doivent échouer bruyamment
    (FieldError) plutôt que renvoyer silencieusement 0 résultat."""

    def test_icontains_leve_fielderror(self):
        with self.assertRaises(FieldError):
            list(Abonne.objects.filter(nom__icontains="a"))

    def test_exact_leve_fielderror(self):
        """Même '=exact' est impossible : chiffrer deux fois "Doe" ne donne
        jamais le même texte chiffré que celui stocké en base."""
        with self.assertRaises(FieldError):
            list(Abonne.objects.filter(telephone_whatsapp="+237690000000"))

    def test_isnull_reste_autorise(self):
        """`isnull` ne compare aucun contenu chiffré : doit rester utilisable."""
        Abonne.objects.create(numero_abonne="AB-1006", nom="Z", prenom="Z", telephone_whatsapp="+237690000015")
        self.assertEqual(Abonne.objects.filter(nom__isnull=True).count(), 0)


class FernetKeyConfigurationTests(TestCase):
    """PII_ENCRYPTION_KEY doit être fail-fast, comme les autres secrets du
    projet (INTERNAL_GRPC_KEY) — jamais de repli silencieux sur une clé par
    défaut connue de tous."""

    def tearDown(self):
        fields._fernet.cache_clear()

    @override_settings(PII_ENCRYPTION_KEY="")
    def test_cle_absente_leve_improperly_configured(self):
        fields._fernet.cache_clear()
        with self.assertRaises(ImproperlyConfigured):
            Abonne.objects.create(numero_abonne="AB-1007", nom="A", prenom="B", telephone_whatsapp="+237690000016")

    @override_settings(PII_ENCRYPTION_KEY="pas-une-cle-fernet-valide")
    def test_cle_mal_formee_leve_improperly_configured(self):
        fields._fernet.cache_clear()
        with self.assertRaises(ImproperlyConfigured):
            Abonne.objects.create(numero_abonne="AB-1008", nom="A", prenom="B", telephone_whatsapp="+237690000017")
