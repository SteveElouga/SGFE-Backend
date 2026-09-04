"""Tests du chiffrement au repos du numéro de téléphone (notifications/fields.py).

Même niveau de rigueur que `services/abonne/abonnes/tests/test_fields.py`
pour le même mécanisme (Fernet, EncryptedCharField)."""

from django.core.exceptions import FieldError, ImproperlyConfigured
from django.db import connection
from django.test import TestCase, override_settings

from notifications import fields
from notifications.models import Diffusion, DiffusionEnvoi, Envoi, TypeEnvoi


class EncryptedFieldTransparencyTests(TestCase):
    """Le chiffrement doit être invisible pour le code applicatif : on écrit
    et on lit des chaînes en clair, comme avant."""

    def test_round_trip_plain_text_via_orm_envoi(self) -> None:
        Envoi.objects.create(
            facture_id="fact-1",
            abonne_id="abonne-1",
            type_envoi=TypeEnvoi.FACTURE,
            telephone="+237690000010",
        )
        envoi = Envoi.objects.get(facture_id="fact-1")
        self.assertEqual(envoi.telephone, "+237690000010")

    def test_round_trip_plain_text_via_orm_diffusion_envoi(self) -> None:
        diffusion = Diffusion.objects.create(message="Coupure d'eau prévue demain")
        DiffusionEnvoi.objects.create(diffusion=diffusion, abonne_id="abonne-2", telephone="+237690000011")
        diffusion_envoi = DiffusionEnvoi.objects.get(abonne_id="abonne-2")
        self.assertEqual(diffusion_envoi.telephone, "+237690000011")

    def test_telephone_vide_reste_chaine_vide(self) -> None:
        """Un token Fernet pour une chaîne vide serait absurde (et le champ ne
        serait plus jamais "vide" au sens applicatif) : le champ chiffré doit
        laisser passer '' tel quel."""
        envoi = Envoi.objects.create(
            facture_id="fact-2", abonne_id="abonne-3", type_envoi=TypeEnvoi.FACTURE, telephone=""
        )
        envoi.refresh_from_db()
        self.assertEqual(envoi.telephone, "")

    def test_valeur_stockee_en_base_est_bien_chiffree(self) -> None:
        """Vérifie directement la colonne en base (hors ORM, donc hors
        déchiffrement automatique) : le numéro en clair ne doit PAS y
        apparaître tel quel."""
        Envoi.objects.create(
            facture_id="fact-3",
            abonne_id="abonne-4",
            type_envoi=TypeEnvoi.FACTURE,
            telephone="+237699999999",
        )
        with connection.cursor() as cursor:
            cursor.execute("SELECT telephone FROM envois WHERE facture_id = %s", ["fact-3"])
            (raw_telephone,) = cursor.fetchone()
        self.assertNotEqual(raw_telephone, "+237699999999")
        self.assertNotIn("699999999", raw_telephone)

    def test_deux_chiffrements_de_la_meme_valeur_different(self) -> None:
        """Fernet est non déterministe (IV + horodatage aléatoires) — deux
        lignes avec le même numéro ne doivent pas produire le même texte
        chiffré (sinon on pourrait corréler deux envois par simple égalité de
        colonne chiffrée, ce qui romprait la confidentialité)."""
        Envoi.objects.create(
            facture_id="fact-4", abonne_id="abonne-5", type_envoi=TypeEnvoi.FACTURE, telephone="+237690000099"
        )
        Envoi.objects.create(
            facture_id="fact-5", abonne_id="abonne-5", type_envoi=TypeEnvoi.FACTURE, telephone="+237690000099"
        )
        with connection.cursor() as cursor:
            cursor.execute("SELECT telephone FROM envois WHERE facture_id IN (%s, %s)", ["fact-4", "fact-5"])
            values = {row[0] for row in cursor.fetchall()}
        self.assertEqual(len(values), 2)


class EncryptedFieldLookupTests(TestCase):
    """Un contenu chiffré non déterministe ne peut pas être filtré en base —
    voir la doc de notifications/fields.py. Ces filtres doivent échouer
    bruyamment (FieldError) plutôt que renvoyer silencieusement 0 résultat."""

    def test_icontains_leve_fielderror(self) -> None:
        # **kwargs plutôt que `telephone__icontains=...` littéral : le plugin
        # mypy django-stubs résout statiquement un lookup littéral en appelant
        # `field.get_lookup(...)` au moment du typage — qui lève ici (c'est le
        # comportement testé), plantant mypy avec une INTERNAL ERROR plutôt que
        # de rapporter proprement une erreur de type. Le déballage dynamique
        # produit un appel strictement équivalent à l'exécution, hors de portée
        # de cette analyse statique spécifique.
        with self.assertRaises(FieldError):
            list(Envoi.objects.filter(**{"telephone__icontains": "690"}))

    def test_exact_leve_fielderror(self) -> None:
        """Même '=exact' est impossible : chiffrer deux fois le même numéro ne
        donne jamais le même texte chiffré que celui stocké en base."""
        with self.assertRaises(FieldError):
            list(Envoi.objects.filter(**{"telephone": "+237690000000"}))

    def test_isnull_reste_autorise(self) -> None:
        """`isnull` ne compare aucun contenu chiffré : doit rester utilisable."""
        Envoi.objects.create(
            facture_id="fact-6", abonne_id="abonne-6", type_envoi=TypeEnvoi.FACTURE, telephone="+237690000098"
        )
        self.assertEqual(Envoi.objects.filter(telephone__isnull=True).count(), 0)


class FernetKeyConfigurationTests(TestCase):
    """PII_ENCRYPTION_KEY doit être fail-fast, comme les autres secrets du
    projet (INTERNAL_GRPC_KEY) — jamais de repli silencieux sur une clé par
    défaut connue de tous."""

    def tearDown(self) -> None:
        fields._fernet.cache_clear()

    @override_settings(PII_ENCRYPTION_KEY="")
    def test_cle_absente_leve_improperly_configured(self) -> None:
        fields._fernet.cache_clear()
        with self.assertRaises(ImproperlyConfigured):
            Envoi.objects.create(
                facture_id="fact-7", abonne_id="abonne-7", type_envoi=TypeEnvoi.FACTURE, telephone="+237690000097"
            )

    @override_settings(PII_ENCRYPTION_KEY="pas-une-cle-fernet-valide")
    def test_cle_mal_formee_leve_improperly_configured(self) -> None:
        fields._fernet.cache_clear()
        with self.assertRaises(ImproperlyConfigured):
            Envoi.objects.create(
                facture_id="fact-8", abonne_id="abonne-8", type_envoi=TypeEnvoi.FACTURE, telephone="+237690000096"
            )
