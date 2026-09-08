"""Champs Django chiffrés au repos — PII utilisateur interne (`email` et
`phone_number` du modèle `User` ; voir la tâche de remédiation « Chiffrement
PII incomplet », `docs/CONFORMITE_SOC2_OWASP.md` §3.1 A02 item 5).

Mécanisme repris À L'IDENTIQUE de `services/abonne/abonnes/fields.py` /
`services/notification/notifications/fields.py` — ne rien réinventer, ce
module en est une duplication fidèle pour tout ce qui concerne
`EncryptedCharField`/`EncryptedTextField` (chaque service Django de ce dépôt
est un projet isolé avec son propre contexte de build Docker, voir CLAUDE.md
racine, d'où la duplication plutôt qu'un import inter-services).

Choix : un champ personnalisé basé sur `cryptography.fernet.Fernet` plutôt
que `django-cryptography`. Deux raisons :
  - `django-cryptography` est peu maintenu (dernière publication ancienne) et
    sa compatibilité avec Django 5.2 n'est pas garantie ; `cryptography` est
    en revanche un standard de facto de l'écosystème Python, activement
    maintenu et audité.
  - Le besoin de chiffrement lui-même est simple (chiffrer/déchiffrer un
    champ texte) : Fernet (AES-128-CBC + HMAC-SHA256, chiffrement symétrique
    authentifié) suffit largement, pas besoin d'un champ de plus haut niveau.

Clé de chiffrement : lue depuis la variable d'environnement
`PII_ENCRYPTION_KEY` (`auth/settings.py`), **jamais codée en dur**. Comme
`INTERNAL_GRPC_KEY`, la vérification est fail-fast : le service refuse de
chiffrer/déchiffrer si la clé est absente, plutôt que d'utiliser une clé par
défaut connue de tous — ce qui ne protégerait rien.

DIFFÉRENCE avec `abonnes/fields.py`/`notifications/fields.py` — recherche
exacte et unicité réellement nécessaires ici :

Contrairement à `nom`/`prenom`/`telephone_whatsapp`/`adresse` côté Abonné, ou
`telephone` côté Notification (jamais filtrés ni uniques), `email` et
`phone_number` du modèle `User` portaient `unique=True` AVANT ce changement,
et sont réellement utilisés en lookup exact (recherche exhaustive menée avant
ce changement, voir ci-dessous) :

  grep -rn "email__\\|phone_number__\\|\\.filter(.*email\\|\\.filter(.*phone_number\\|
  \\.get(.*email\\|\\.get(.*phone_number\\|\\.exclude(.*email\\|\\.exclude(.*phone_number\\|
  Q(email\\|Q(phone_number\\|order_by(.*email\\|order_by(.*phone_number" services/auth/

  puis relecture manuelle de `comptes/repositories.py`, `comptes/services.py`,
  `comptes/grpc_server.py`, `comptes/serializers.py`, `comptes/export.py`,
  `comptes/dtos.py`. Résultat — TROIS lookups exacts réels, tous dans
  `UserRepository` (`comptes/repositories.py`), rien ailleurs :

  1. `get_by_email(email)` → `User.objects.get(email=email)`, appelé par
     `PasswordSetupService.request_password_reset` (réinitialisation de mot
     de passe par e-mail, flux ADMIN).
  2. `get_by_phone(phone_number)` → `User.objects.get(phone_number=phone_number)`,
     appelé par `PhoneOtpService.request_otp_by_phone` et
     `PhoneOtpService.verify_otp_and_set_password` (OTP WhatsApp
     d'activation/réinitialisation, tous rôles).
  3. `get_by_username_or_phone(identifier)` → `User.objects.get(Q(username=identifier)
     | Q(phone_number=identifier))`, appelé par `AuthService.login` (un
     utilisateur peut se connecter avec son username OU son numéro de
     téléphone).

  Aucun `order_by`/`icontains`/tri sur ces deux champs nulle part — seuls des
  lookups d'égalité stricte, et les deux contraintes `unique=True` du modèle.
  `serializers.py`, `export.py`, `dtos.py`, `grpc_server.py` ne font que LIRE
  la valeur en clair d'une instance déjà chargée (transparent avec
  `EncryptedCharField`, aucun changement requis là).

Un champ chiffré Fernet est un chiffrement AUTHENTIFIÉ NON DÉTERMINISTE (IV +
horodatage aléatoires à chaque appel) : `WHERE email = %s` ou une contrainte
`unique=True` sur la colonne chiffrée ne fonctionnent JAMAIS (deux
chiffrements de la même valeur produisent des textes chiffrés différents).
Contrairement à Abonné/Notification, où ce constat suffisait à conclure
« recherche/unicité non nécessaires, donc pas de problème », ici les TROIS
lookups ci-dessus et les DEUX contraintes `unique=True` sont réellement
utilisés — chiffrer purement et simplement `email`/`phone_number` aurait
cassé le login par téléphone, la réinitialisation de mot de passe et l'OTP
WhatsApp, et aurait fait disparaître la protection anti-doublon (deux
utilisateurs pourraient s'inscrire avec le même numéro, chacun produisant un
texte chiffré différent — la contrainte SQL ne les distinguerait plus).

Solution retenue — hash de recherche déterministe en colonne séparée :
en plus du chiffrement Fernet (confidentialité), chaque champ porte un champ
compagnon `<champ>_hash` (`email_hash`, `phone_number_hash`), un
HMAC-SHA256 hexadécimal de la valeur EN CLAIR, calculé automatiquement à
l'écriture (voir `User.save()`, `comptes/models.py`). C'est ce champ `_hash`
qui porte désormais la contrainte `unique=True` et qui sert de cible à tout
lookup exact (`UserRepository.get_by_email`/`get_by_phone`/
`get_by_username_or_phone` interrogent `email_hash=hash_email(...)`/
`phone_number_hash=hash_phone(...)`, jamais `email=...`/`phone_number=...`).

HMAC plutôt qu'un simple `hashlib.sha256(value)` : un numéro de téléphone
camerounais (`+2376XXXXXXXX`, 9 chiffres utiles dont le premier fixé à 6, cf.
`comptes/validators.py::validate_phone_cameroon`) a une entropie beaucoup
trop faible pour un hash nu — une table précalculée (rainbow table/attaque
par dictionnaire) sur l'espace entier des numéros valides retrouverait
instantanément la valeur en clair à partir du hash stocké en base. HMAC avec
une clé secrète DÉDIÉE (`PII_LOOKUP_HMAC_KEY`, lue comme `PII_ENCRYPTION_KEY`
mais JAMAIS la même valeur — on ne réutilise jamais une même clé pour deux
primitives cryptographiques différentes, ici chiffrement symétrique vs.
authentification de message) empêche ce précalcul : sans connaître la clé,
retrouver la valeur en clair à partir du hash exige de la deviner en plus de
la valeur elle-même.

Ce hash NE remplace PAS le chiffrement Fernet : il ne sert qu'à la recherche
exacte/l'unicité, jamais à retrouver la valeur en clair (HMAC n'est pas
réversible) — `email`/`phone_number` restent les champs à lire pour obtenir
la valeur métier, `email_hash`/`phone_number_hash` ne sont jamais exposés en
dehors de ce mécanisme (absents de `dtos.py`/`serializers.py`/`export.py`).

Limite assumée, inchangée par rapport à Abonné/Notification : toute
recherche PARTIELLE (`icontains`, tri alphabétique) reste impossible sur
`email`/`phone_number` eux-mêmes — seule l'égalité stricte est couverte par
le hash. Aucun besoin de ce type n'existe aujourd'hui (recherche ci-dessus) ;
si un jour un besoin de préfixe/sous-chaîne apparaît, il faudra rapatrier les
lignes candidates et comparer en mémoire, le hash ne le résout pas.
"""

from __future__ import annotations

import hashlib
import hmac
from functools import lru_cache
from typing import Any

from cryptography.fernet import Fernet, InvalidToken
from django.conf import settings
from django.core.exceptions import FieldError, ImproperlyConfigured
from django.db import models


@lru_cache(maxsize=1)
def _fernet() -> Fernet:
    """Construit le Fernet à partir de `PII_ENCRYPTION_KEY`. Mis en cache
    (clé lue une seule fois par process) — `lru_cache` sur une fonction sans
    argument équivaut ici à un singleton paresseux."""
    key = getattr(settings, "PII_ENCRYPTION_KEY", None)
    if not key:
        raise ImproperlyConfigured(
            "PII_ENCRYPTION_KEY est obligatoire pour chiffrer les données personnelles "
            "des utilisateurs (e-mail, numéro de téléphone) — voir .env.example. "
            'Générer une clé : python -c "from cryptography.fernet import Fernet; '
            'print(Fernet.generate_key().decode())"'
        )
    key_bytes = key.encode("utf-8") if isinstance(key, str) else key
    try:
        return Fernet(key_bytes)
    except (ValueError, TypeError) as exc:
        raise ImproperlyConfigured(
            "PII_ENCRYPTION_KEY invalide — attendu 32 octets encodés en base64 urlsafe "
            "(format produit par Fernet.generate_key())."
        ) from exc


@lru_cache(maxsize=1)
def _hmac_key() -> bytes:
    """Construit la clé HMAC à partir de `PII_LOOKUP_HMAC_KEY`. Mis en cache,
    même principe que `_fernet()` ci-dessus.

    DÉLIBÉRÉMENT une variable d'environnement distincte de
    `PII_ENCRYPTION_KEY` — voir le docstring de tête de ce module : ne jamais
    réutiliser une même clé pour deux primitives cryptographiques
    différentes (chiffrement symétrique vs. authentification de message).
    """
    key = getattr(settings, "PII_LOOKUP_HMAC_KEY", None)
    if not key:
        raise ImproperlyConfigured(
            "PII_LOOKUP_HMAC_KEY est obligatoire pour calculer le hash de recherche "
            "déterministe des champs PII uniques (e-mail, numéro de téléphone) — voir "
            '.env.example. Générer une clé : python -c "import secrets; '
            'print(secrets.token_urlsafe(32))"'
        )
    return key.encode("utf-8") if isinstance(key, str) else key


def compute_lookup_hash(value: str) -> str:
    """HMAC-SHA256 déterministe (hexadécimal, 64 caractères) d'une valeur PII
    en clair — voir le docstring de tête de ce module pour la justification
    complète (recherche exacte/unicité sur un champ par ailleurs chiffré avec
    Fernet, non déterministe ; HMAC plutôt qu'un sha256 nu à cause de la
    faible entropie d'un numéro de téléphone).
    """
    return hmac.new(_hmac_key(), value.encode("utf-8"), hashlib.sha256).hexdigest()


def hash_email(email: str) -> str:
    """Hash de recherche déterministe pour `User.email_hash` — voir
    `compute_lookup_hash`. Utilisé par `User.save()` (calcul automatique) et
    par `UserRepository.get_by_email` (lookup)."""
    return compute_lookup_hash(email)


def hash_phone(phone_number: str) -> str:
    """Hash de recherche déterministe pour `User.phone_number_hash` — voir
    `compute_lookup_hash`. Utilisé par `User.save()` (calcul automatique) et
    par `UserRepository.get_by_phone`/`get_by_username_or_phone` (lookup)."""
    return compute_lookup_hash(phone_number)


class _EncryptedFieldMixin:
    """Chiffre à l'écriture (`get_prep_value`, appelé juste avant l'I/O DB),
    déchiffre à la lecture (`from_db_value`, appelé juste après). Transparent
    pour le reste du code applicatif : `user.email`/`user.phone_number`
    restent des `str` en clair dans tout le code Python (services,
    sérialiseurs, gRPC) — seule la colonne en base contient le token Fernet.

    Mixin pur (pas de base `models.Field` ici) : `name` est déclaré ci-dessous
    pour mypy, et les appels `super()` vers `get_prep_value`/`get_lookup` sont
    `# type: ignore[misc]` — mypy ne peut pas résoudre statiquement le membre
    apporté par l'autre base (`models.CharField`/`models.TextField`) dans
    l'ordre de résolution des classes des sous-classes concrètes ci-dessous ;
    à l'exécution, `super()` s'y résout normalement (MRO réel des instances).
    """

    name: str

    def get_prep_value(self, value: Any) -> Any:
        value = super().get_prep_value(value)  # type: ignore[misc]
        if value is None or value == "":
            return value
        return _fernet().encrypt(value.encode("utf-8")).decode("utf-8")

    def from_db_value(self, value: Any, expression: Any, connection: Any) -> Any:
        if value is None or value == "":
            return value
        try:
            return _fernet().decrypt(value.encode("utf-8")).decode("utf-8")
        except InvalidToken:
            # Ne masque jamais l'erreur : une valeur illisible (mauvaise clé,
            # ou ligne jamais rechiffrée par la migration de données) doit
            # planter bruyamment plutôt que de renvoyer un texte chiffré/
            # corrompu comme si c'était l'e-mail/le téléphone de l'utilisateur.
            raise

    def get_lookup(self, lookup_name: str) -> Any:
        # Fernet est un chiffrement AUTHENTIFIÉ NON DÉTERMINISTE (IV + horodatage
        # aléatoires à chaque appel) : chiffrer deux fois la même valeur produit
        # un texte chiffré différent. Un `WHERE email = %s` ou `LIKE %%%s%%`
        # contre la colonne chiffrée ne matcherait donc JAMAIS la bonne ligne —
        # et le ferait silencieusement (0 résultat, pas d'erreur). On préfère un
        # échec bruyant et explicite à l'ouverture d'une telle requête plutôt que
        # de laisser une future recherche par e-mail/téléphone échouer en
        # silence en prod. `isnull` reste sûr : il ne compare aucun contenu
        # chiffré. Le lookup exact réel se fait via `email_hash`/
        # `phone_number_hash` (voir tête de module) — jamais ce champ.
        if lookup_name != "isnull":
            raise FieldError(
                f"Le champ {self.name!r} est chiffré au repos (Fernet, non déterministe) : "
                f"le filtre '{lookup_name}' est impossible en base, y compris '=exact' "
                "(chiffrer deux fois la même valeur donne un texte chiffré différent). "
                "Utiliser le champ de hash de recherche compagnon "
                f"('{self.name}_hash', voir comptes/fields.py::hash_email/hash_phone) "
                "pour un lookup exact, ou récupérer les lignes candidates puis comparer "
                "en clair en mémoire pour tout autre besoin."
            )
        return super().get_lookup(lookup_name)  # type: ignore[misc]


class EncryptedCharField(_EncryptedFieldMixin, models.CharField):  # type: ignore[type-arg]
    # ^ `models.CharField` n'est générique que dans les stubs django-stubs, pas à
    # l'exécution (non souscriptable dans le vrai Django) : impossible d'écrire
    # `CharField[str, str]` sans planter l'import réel — voir le même choix pour
    # `EncryptedTextField` ci-dessous.
    """CharField chiffré au repos.

    `max_length` continue de porter sur la valeur EN CLAIR — c'est la
    longueur métier voulue (validée par `full_clean()`/formulaires) — mais la
    colonne réelle est un `TEXT` sans contrainte de longueur : un token
    Fernet (IV + HMAC + padding + horodatage, le tout en base64) dépasse
    largement `max_length` pour des champs courts (ex. ~226 caractères de
    ciphertext pour 100 caractères de texte en clair).
    """

    def db_type(self, connection: Any) -> str:
        return "text"


class EncryptedTextField(_EncryptedFieldMixin, models.TextField):  # type: ignore[type-arg]
    """TextField chiffré au repos (déjà `TEXT` en base, aucun changement de
    type de colonne nécessaire — seul le contenu devient un token Fernet).

    Non utilisé aujourd'hui dans ce service (`email`/`phone_number` sont tous
    deux des `EncryptedCharField`) — conservé pour la symétrie avec
    `abonnes/fields.py`/`notifications/fields.py` et une éventuelle extension
    future.
    """
