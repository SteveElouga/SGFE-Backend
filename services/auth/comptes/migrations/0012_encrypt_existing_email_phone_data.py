# Migration de DONNÉES : rechiffre les valeurs existantes des colonnes
# `email`/`phone_number` de la table `users` après le changement de schéma de
# 0011_encrypt_email_phone_fields, ET calcule leur hash de recherche
# déterministe (`email_hash`/`phone_number_hash`) — les deux opérations se
# font dans la même passe pour ne lire chaque ligne qu'une fois.
#
# Pourquoi du SQL brut plutôt que l'ORM (`apps.get_model`) : à ce point de
# l'historique des migrations, le modèle historique reconstruit par Django
# utilise déjà `EncryptedCharField` (0011 est déjà appliquée) — lire
# `user.email`/`user.phone_number` via l'ORM tenterait donc de DÉCHIFFRER une
# valeur qui est encore en clair, et lèverait `InvalidToken` sur la toute
# première ligne. Cette migration lit/écrit directement les colonnes, en
# contournant `from_db_value`/`get_prep_value` — même patron que
# services/notification/notifications/migrations/
# 0008_encrypt_existing_telephone_data.py.
#
# Idempotente : si une valeur est déjà un token Fernet valide (relance de la
# migration, ou table déjà chiffrée), elle n'est pas rechiffrée une seconde
# fois — mais son hash est quand même recalculé à chaque passage (le
# déchiffrement redonne le texte en clair nécessaire), pour rester correct
# même si `PII_LOOKUP_HMAC_KEY` a changé entre deux exécutions (ex. rotation).
#
# Contexte dépôt (voir MEMORY.md) : les données de démo ont été purgées le
# 2026-08-27 et aucun seed n'a été relancé depuis — la table `users` de cet
# environnement de dev est donc probablement réduite aux comptes créés
# manuellement depuis. Elle reste néanmoins écrite pour être correcte sur une
# base non vide (env. d'un autre développeur, ou production) : ne JAMAIS
# supposer une base vierge dans une migration.
from __future__ import annotations

import hashlib
import hmac

from cryptography.fernet import Fernet, InvalidToken
from django.conf import settings
from django.core.exceptions import ImproperlyConfigured
from django.db import migrations


def _build_fernet() -> Fernet:
    key = getattr(settings, "PII_ENCRYPTION_KEY", None)
    if not key:
        raise ImproperlyConfigured(
            "PII_ENCRYPTION_KEY est requis pour exécuter la migration "
            "0012_encrypt_existing_email_phone_data (rechiffrement des e-mails/numéros "
            "de téléphone existants)."
        )
    return Fernet(key.encode("utf-8") if isinstance(key, str) else key)


def _build_hmac_key() -> bytes:
    key = getattr(settings, "PII_LOOKUP_HMAC_KEY", None)
    if not key:
        raise ImproperlyConfigured(
            "PII_LOOKUP_HMAC_KEY est requis pour exécuter la migration "
            "0012_encrypt_existing_email_phone_data (calcul du hash de recherche des "
            "e-mails/numéros de téléphone existants)."
        )
    return key.encode("utf-8") if isinstance(key, str) else key


def _compute_hash(hmac_key: bytes, plaintext: str) -> str:
    return hmac.new(hmac_key, plaintext.encode("utf-8"), hashlib.sha256).hexdigest()


def _plaintext_and_ciphertext(fernet: Fernet, value: str | None) -> tuple[str | None, str | None]:
    """Retourne `(plaintext, ciphertext)` pour une valeur qui peut être déjà
    chiffrée (relance de la migration) ou encore en clair (première
    exécution). `plaintext` sert au calcul du hash, `ciphertext` est ce qui
    est réécrit en base."""
    if value is None or value == "":
        return None, value
    try:
        plaintext = fernet.decrypt(value.encode("utf-8")).decode("utf-8")
        return plaintext, value  # Déjà chiffré — ciphertext inchangé.
    except (InvalidToken, ValueError):
        return value, fernet.encrypt(value.encode("utf-8")).decode("utf-8")


def encrypt_existing_email_phone(apps, schema_editor) -> None:
    fernet = _build_fernet()
    hmac_key = _build_hmac_key()

    with schema_editor.connection.cursor() as cursor:
        cursor.execute("SELECT id, email, phone_number FROM users")
        rows = cursor.fetchall()
        for user_id, email, phone_number in rows:
            email_plaintext, email_ciphertext = _plaintext_and_ciphertext(fernet, email)
            phone_plaintext, phone_ciphertext = _plaintext_and_ciphertext(fernet, phone_number)

            email_hash = _compute_hash(hmac_key, email_plaintext) if email_plaintext else None
            # phone_number est obligatoire (jamais vide/NULL en usage normal —
            # voir comptes/models.py) ; le cas défensif `None` ne devrait pas
            # se produire mais évite un `AttributeError` si une ligne
            # incohérente existait malgré tout.
            phone_hash = _compute_hash(hmac_key, phone_plaintext) if phone_plaintext else None

            cursor.execute(
                "UPDATE users SET email = %s, email_hash = %s, phone_number = %s, "
                "phone_number_hash = %s WHERE id = %s",
                [email_ciphertext, email_hash, phone_ciphertext, phone_hash, user_id],
            )


def refuse_reverse(apps, schema_editor) -> None:
    # Pas de vrai "reverse" : redéchiffrer en clair recréerait le trou de
    # sécurité que cette migration ferme. `RunPython.noop` documenterait mal
    # l'intention (on ne l'a pas oublié, on le refuse) — un message explicite
    # est préférable si jamais quelqu'un tente `migrate comptes 0011`.
    raise RuntimeError(
        "0012_encrypt_existing_email_phone_data ne peut pas être inversée : redéchiffrer "
        "les e-mails/numéros de téléphone en clair en base recréerait la vulnérabilité que "
        "cette migration corrige."
    )


class Migration(migrations.Migration):

    dependencies = [
        ("comptes", "0011_encrypt_email_phone_fields"),
    ]

    operations = [
        migrations.RunPython(encrypt_existing_email_phone, refuse_reverse),
    ]
