"""Champs Django chiffrés au repos — PII notification (numéro de téléphone du
destinataire WhatsApp, champ `telephone` des modèles `Envoi` et
`DiffusionEnvoi` ; voir la tâche de remédiation « Chiffrement PII incomplet »,
`docs/CONFORMITE_SOC2_OWASP.md` §3.1 A02 item 5).

Mécanisme repris À L'IDENTIQUE de `services/abonne/abonnes/fields.py` — ne
rien réinventer, ce module en est une duplication fidèle (chaque service
Django de ce dépôt est un projet isolé avec son propre contexte de build
Docker, voir CLAUDE.md racine, d'où la duplication plutôt qu'un import
inter-services).

Choix : un champ personnalisé basé sur `cryptography.fernet.Fernet` plutôt
que `django-cryptography`. Deux raisons :
  - `django-cryptography` est peu maintenu (dernière publication ancienne) et
    sa compatibilité avec Django 5.2 n'est pas garantie ; `cryptography` est
    en revanche un standard de facto de l'écosystème Python, activement
    maintenu et audité.
  - Le besoin est simple (chiffrer/déchiffrer un champ texte, aucune
    recherche ni tri en base sur le contenu en clair — voir plus bas) :
    Fernet (AES-128-CBC + HMAC-SHA256, chiffrement symétrique authentifié)
    suffit largement, pas besoin d'un champ de plus haut niveau.

Clé : lue depuis la variable d'environnement `PII_ENCRYPTION_KEY`
(`notification/settings.py`), **jamais codée en dur**. Comme
`INTERNAL_GRPC_KEY`, `env()` est appelé sans valeur par défaut : le service
refuse de démarrer si la clé est absente (fail-fast), plutôt que de chiffrer
avec une clé par défaut connue de tous — ce qui ne protégerait rien.

Limite assumée — recherche/LIKE impossible sur ce champ : un champ chiffré
ne peut plus être filtré par le SGBD (`telephone__icontains=...`, tri
alphabétique, etc.), le contenu en base n'étant plus le texte en clair.
Vérifié avant ce changement (`grep -rn "telephone__\\|\\.filter(.*telephone\\|
\\.get(.*telephone\\|\\.exclude(.*telephone\\|Q(telephone\\|order_by(.*telephone"
services/notification/`, plus une relecture manuelle de `notifications/
services.py` et `notifications/repositories.py`) : **aucun filtre, tri ni
contrainte d'unicité de ce type n'existe** sur `telephone` dans ce service —
il n'est jamais utilisé qu'en écriture (création d'un `Envoi`/
`DiffusionEnvoi`) ou transmis tel quel au client WhatsApp
(`whatsapp_client.send`). Cette limite est donc théorique aujourd'hui ; si un
filtre par téléphone est ajouté plus tard, il devra rapatrier les lignes
candidates puis comparer en mémoire (ou maintenir un index séparé non
chiffré, ex. un hash déterministe du numéro) — pas un `WHERE telephone LIKE
%...%` direct.
"""

from __future__ import annotations

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
            "transitant par ce service (numéro de téléphone WhatsApp) — voir .env.example. "
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


class _EncryptedFieldMixin:
    """Chiffre à l'écriture (`get_prep_value`, appelé juste avant l'I/O DB),
    déchiffre à la lecture (`from_db_value`, appelé juste après). Transparent
    pour le reste du code applicatif : `envoi.telephone` reste une `str` en
    clair dans tout le code Python (services, sérialiseurs, gRPC) — seule la
    colonne en base contient le token Fernet.

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
            # corrompu comme si c'était le numéro de téléphone du destinataire.
            raise

    def get_lookup(self, lookup_name: str) -> Any:
        # Fernet est un chiffrement AUTHENTIFIÉ NON DÉTERMINISTE (IV + horodatage
        # aléatoires à chaque appel) : chiffrer deux fois la même valeur produit
        # un texte chiffré différent. Un `WHERE telephone = %s` ou `LIKE %%%s%%`
        # contre la colonne chiffrée ne matcherait donc JAMAIS la bonne ligne —
        # et le ferait silencieusement (0 résultat, pas d'erreur). On préfère un
        # échec bruyant et explicite à l'ouverture d'une telle requête plutôt que
        # de laisser une future recherche "par téléphone" échouer en silence en
        # prod. `isnull` reste sûr : il ne compare aucun contenu chiffré.
        if lookup_name != "isnull":
            raise FieldError(
                f"Le champ {self.name!r} est chiffré au repos (Fernet, non déterministe) : "
                f"le filtre '{lookup_name}' est impossible en base, y compris '=exact' "
                "(chiffrer deux fois la même valeur donne un texte chiffré différent). "
                "Récupérer les lignes candidates puis comparer en clair en mémoire, ou "
                "ajouter un champ séparé non chiffré (ex. hash déterministe) si une "
                "recherche/un filtre est réellement nécessaire — voir notifications/fields.py."
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

    Non utilisé aujourd'hui dans ce service (aucun champ PII de type texte
    long) — conservé pour la symétrie avec `abonnes/fields.py` et une
    éventuelle extension future.
    """
