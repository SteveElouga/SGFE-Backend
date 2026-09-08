"""Tests du chiffrement au repos des PII utilisateur (comptes/fields.py) —
`email`/`phone_number` du modèle `User`.

Même niveau de rigueur que `services/abonne/abonnes/tests/test_fields.py` /
`services/notification/notifications/tests/test_fields.py` pour le mécanisme
de chiffrement Fernet lui-même, PLUS une section dédiée au hash de recherche
déterministe (`compute_lookup_hash`/`hash_email`/`hash_phone`), absent des
deux autres services (eux n'ont jamais eu besoin de recherche exacte/unicité
sur un champ chiffré — voir comptes/fields.py, tête de module)."""

from django.core.exceptions import FieldError, ImproperlyConfigured
from django.db import connection
from django.test import TestCase, override_settings

from comptes import fields
from comptes.models import Role, User


class EncryptedFieldTransparencyTests(TestCase):
    """Le chiffrement doit être invisible pour le code applicatif : on écrit
    et on lit des chaînes en clair, comme avant."""

    def test_round_trip_plain_text_via_orm(self) -> None:
        User.objects.create_user(
            username="transp1", email="transp1@example.com", phone_number="+237690001001", role=Role.ADMIN
        )
        user = User.objects.get(username="transp1")
        self.assertEqual(user.email, "transp1@example.com")
        self.assertEqual(user.phone_number, "+237690001001")

    def test_email_none_reste_none(self) -> None:
        """`email` est nul pour les rôles non-ADMIN — un token Fernet pour une
        valeur nulle serait absurde : le champ chiffré doit laisser passer
        `None` tel quel."""
        user = User.objects.create_user(username="transp2", phone_number="+237690001002", role=Role.AGENT)
        user.refresh_from_db()
        self.assertIsNone(user.email)

    def test_valeur_stockee_en_base_est_bien_chiffree(self) -> None:
        """Vérifie directement la colonne en base (hors ORM, donc hors
        déchiffrement automatique) : la valeur en clair ne doit PAS y
        apparaître telle quelle."""
        User.objects.create_user(
            username="transp3", email="secret42@example.com", phone_number="+237690001003", role=Role.ADMIN
        )
        with connection.cursor() as cursor:
            cursor.execute("SELECT email, phone_number FROM users WHERE username = %s", ["transp3"])
            raw_email, raw_phone = cursor.fetchone()
        self.assertNotEqual(raw_email, "secret42@example.com")
        self.assertNotIn("secret42", raw_email)
        self.assertNotEqual(raw_phone, "+237690001003")
        self.assertNotIn("690001003", raw_phone)

    def test_meme_valeur_en_clair_donne_deux_ciphertexts_differents(self) -> None:
        """Chiffre directement la même chaîne deux fois (sans passer par
        l'unicité du modèle, hors de propos ici) pour vérifier le
        non-déterminisme de Fernet lui-même."""
        token1 = fields._fernet().encrypt(b"+237690009999").decode("utf-8")
        token2 = fields._fernet().encrypt(b"+237690009999").decode("utf-8")
        self.assertNotEqual(token1, token2)
        self.assertEqual(fields._fernet().decrypt(token1.encode()).decode(), "+237690009999")
        self.assertEqual(fields._fernet().decrypt(token2.encode()).decode(), "+237690009999")


class EncryptedFieldLookupTests(TestCase):
    """Un contenu chiffré non déterministe ne peut pas être filtré en base —
    voir la doc de comptes/fields.py. Ces filtres doivent échouer bruyamment
    (FieldError) plutôt que renvoyer silencieusement 0 résultat."""

    def test_icontains_sur_email_leve_fielderror(self) -> None:
        # **kwargs plutôt que `email__icontains=...` littéral : le plugin mypy
        # django-stubs résout statiquement un lookup littéral en appelant
        # `field.get_lookup(...)` au moment du typage — qui lève ici (c'est le
        # comportement testé), plantant mypy avec une INTERNAL ERROR plutôt que
        # de rapporter proprement une erreur de type. Le déballage dynamique
        # produit un appel strictement équivalent à l'exécution, hors de portée
        # de cette analyse statique spécifique.
        with self.assertRaises(FieldError):
            list(User.objects.filter(**{"email__icontains": "a"}))

    def test_exact_sur_email_leve_fielderror(self) -> None:
        """Même '=exact' est impossible sur le champ chiffré lui-même — le
        lookup exact réel passe par `email_hash` (voir test_repositories.py)."""
        with self.assertRaises(FieldError):
            list(User.objects.filter(**{"email": "quelquun@example.com"}))

    def test_exact_sur_phone_number_leve_fielderror(self) -> None:
        with self.assertRaises(FieldError):
            list(User.objects.filter(**{"phone_number": "+237690000000"}))

    def test_isnull_reste_autorise(self) -> None:
        """`isnull` ne compare aucun contenu chiffré : doit rester utilisable."""
        User.objects.create_user(username="lookup1", phone_number="+237690001006", role=Role.AGENT)
        self.assertEqual(User.objects.filter(email__isnull=False).exclude(username="lookup1").count(), 0)


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
            User.objects.create_user(username="nokey1", phone_number="+237690001007", role=Role.AGENT)

    @override_settings(PII_ENCRYPTION_KEY="pas-une-cle-fernet-valide")
    def test_cle_mal_formee_leve_improperly_configured(self) -> None:
        fields._fernet.cache_clear()
        with self.assertRaises(ImproperlyConfigured):
            User.objects.create_user(username="badkey1", phone_number="+237690001008", role=Role.AGENT)


class HmacKeyConfigurationTests(TestCase):
    """PII_LOOKUP_HMAC_KEY doit être aussi fail-fast que PII_ENCRYPTION_KEY —
    même raisonnement, même absence de valeur par défaut silencieuse."""

    def tearDown(self) -> None:
        fields._hmac_key.cache_clear()

    @override_settings(PII_LOOKUP_HMAC_KEY="")
    def test_cle_absente_leve_improperly_configured(self) -> None:
        fields._hmac_key.cache_clear()
        with self.assertRaises(ImproperlyConfigured):
            User.objects.create_user(username="nohmac1", phone_number="+237690001009", role=Role.AGENT)


class LookupHashComputationTests(TestCase):
    """`compute_lookup_hash`/`hash_email`/`hash_phone` : HMAC-SHA256
    déterministe, DISTINCT du chiffrement Fernet non déterministe ci-dessus —
    c'est précisément ce qui permet la recherche exacte/l'unicité."""

    def test_hash_est_deterministe(self) -> None:
        """Contrairement à Fernet, la même valeur donne toujours le même
        hash — c'est ce qui rend le lookup exact possible."""
        self.assertEqual(fields.compute_lookup_hash("+237690000001"), fields.compute_lookup_hash("+237690000001"))
        self.assertEqual(fields.hash_email("a@example.com"), fields.hash_email("a@example.com"))
        self.assertEqual(fields.hash_phone("+237690000001"), fields.hash_phone("+237690000001"))

    def test_hash_differe_selon_la_valeur(self) -> None:
        self.assertNotEqual(fields.hash_phone("+237690000001"), fields.hash_phone("+237690000002"))

    def test_hash_est_hexadecimal_64_caracteres(self) -> None:
        """`max_length=64` sur `email_hash`/`phone_number_hash` (comptes/models.py)
        suppose un hexdigest SHA-256 (64 caractères) — vérifie le format produit."""
        digest = fields.compute_lookup_hash("valeur-quelconque")
        self.assertEqual(len(digest), 64)
        int(digest, 16)  # lève ValueError si ce n'est pas de l'hexadécimal

    @override_settings(PII_LOOKUP_HMAC_KEY="cle-hmac-A")
    def test_hash_depend_de_la_cle(self) -> None:
        """Deux clés HMAC différentes doivent produire deux hash différents
        pour la même valeur en clair — sinon la clé ne sert à rien."""
        fields._hmac_key.cache_clear()
        digest_a = fields.compute_lookup_hash("+237690000005")
        fields._hmac_key.cache_clear()
        with override_settings(PII_LOOKUP_HMAC_KEY="cle-hmac-B"):
            fields._hmac_key.cache_clear()
            digest_b = fields.compute_lookup_hash("+237690000005")
        fields._hmac_key.cache_clear()
        self.assertNotEqual(digest_a, digest_b)


class UserSaveComputesHashTests(TestCase):
    """`User.save()` calcule `email_hash`/`phone_number_hash` à chaque
    écriture, automatiquement — jamais assignés à la main par le code
    applicatif (voir comptes/models.py::User.save)."""

    def test_hash_calcule_a_la_creation(self) -> None:
        user = User.objects.create_user(
            username="hashcreate1", email="hashcreate1@example.com", phone_number="+237690002001", role=Role.ADMIN
        )
        self.assertEqual(user.phone_number_hash, fields.hash_phone("+237690002001"))
        self.assertEqual(user.email_hash, fields.hash_email("hashcreate1@example.com"))

    def test_email_hash_none_si_pas_demail(self) -> None:
        user = User.objects.create_user(username="hashcreate2", phone_number="+237690002002", role=Role.AGENT)
        self.assertIsNone(user.email)
        self.assertIsNone(user.email_hash)

    def test_hash_recalcule_a_la_mise_a_jour(self) -> None:
        """Une mise à jour directe de `user.phone_number` (comme le fait
        `UserAdminService.update_user`/`anonymiser_utilisateur`,
        comptes/services.py) doit rafraîchir le hash au prochain `save()` —
        sans quoi un lookup ultérieur par l'ancien OU le nouveau numéro
        échouerait silencieusement."""
        user = User.objects.create_user(username="hashupdate1", phone_number="+237690002003", role=Role.AGENT)
        ancien_hash = user.phone_number_hash
        user.phone_number = "+237690002004"
        user.save()
        user.refresh_from_db()
        self.assertNotEqual(user.phone_number_hash, ancien_hash)
        self.assertEqual(user.phone_number_hash, fields.hash_phone("+237690002004"))

    def test_deux_utilisateurs_sans_email_ne_collisionnent_pas(self) -> None:
        """`email_hash` est nullable + `unique=True` : deux comptes non-ADMIN
        sans e-mail (donc `email_hash=None` pour les deux) ne doivent JAMAIS
        se heurter à la contrainte d'unicité — PostgreSQL/SQLite traitent NULL
        comme distinct de toute autre valeur, y compris une autre NULL."""
        User.objects.create_user(username="noemail1", phone_number="+237690002005", role=Role.AGENT)
        User.objects.create_user(username="noemail2", phone_number="+237690002006", role=Role.AGENT)
        # Ne doit lever aucune IntegrityError — la seule assertion est que ce
        # bloc s'exécute sans exception.
