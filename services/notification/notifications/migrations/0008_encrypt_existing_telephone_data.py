# Migration de DONNÉES : rechiffre les valeurs existantes de la colonne
# `telephone` des tables `envois` et `diffusion_envois` après le changement de
# schéma de 0007_encrypt_telephone_field.
#
# Pourquoi du SQL brut plutôt que l'ORM (`apps.get_model`) : à ce point de
# l'historique des migrations, le modèle historique reconstruit par Django
# utilise déjà `EncryptedCharField` (0007 est déjà appliquée) — lire
# `envoi.telephone` via l'ORM tenterait donc de DÉCHIFFRER une valeur qui est
# encore en clair, et lèverait `InvalidToken` sur la toute première ligne.
# Cette migration lit/écrit directement la colonne, en contournant
# `from_db_value`/`get_prep_value`.
#
# Idempotente : si une valeur est déjà un token Fernet valide (relance de la
# migration, ou table déjà chiffrée), elle n'est pas rechiffrée une seconde
# fois — un Fernet re-chiffré n'est de toute façon pas un bug de sécurité,
# mais autant éviter le travail inutile et rester prévisible.
#
# Contexte dépôt (voir MEMORY.md) : les données de démo ont été purgées le
# 2026-08-27 et aucun seed n'a été relancé depuis — les tables `envois` et
# `diffusion_envois` de cet environnement de dev sont donc probablement vides
# au moment où cette migration s'exécute ici (0 ligne affectée). Elle reste
# néanmoins écrite pour être correcte sur une base non vide (env. d'un autre
# développeur, ou production) : ne JAMAIS supposer une base vierge dans une
# migration.
from __future__ import annotations

from cryptography.fernet import Fernet, InvalidToken
from django.conf import settings
from django.core.exceptions import ImproperlyConfigured
from django.db import migrations


def _build_fernet() -> Fernet:
    key = getattr(settings, "PII_ENCRYPTION_KEY", None)
    if not key:
        raise ImproperlyConfigured(
            "PII_ENCRYPTION_KEY est requis pour exécuter la migration "
            "0008_encrypt_existing_telephone_data (rechiffrement des numéros de "
            "téléphone existants)."
        )
    return Fernet(key.encode("utf-8") if isinstance(key, str) else key)


def _encrypt_if_needed(fernet: Fernet, value: str | None) -> str | None:
    if value is None or value == "":
        return value
    try:
        fernet.decrypt(value.encode("utf-8"))
        return value  # Déjà un token Fernet valide — rien à faire.
    except (InvalidToken, ValueError):
        return fernet.encrypt(value.encode("utf-8")).decode("utf-8")


def _encrypt_table_column(fernet: Fernet, schema_editor, table: str) -> None:
    """Rechiffre la colonne `telephone` d'une table donnée, ligne par ligne."""
    with schema_editor.connection.cursor() as cursor:
        cursor.execute(f"SELECT id, telephone FROM {table}")
        rows = cursor.fetchall()
        for row_id, telephone in rows:
            cursor.execute(
                f"UPDATE {table} SET telephone = %s WHERE id = %s",
                [_encrypt_if_needed(fernet, telephone), row_id],
            )


def encrypt_existing_telephone(apps, schema_editor) -> None:
    fernet = _build_fernet()
    _encrypt_table_column(fernet, schema_editor, "envois")
    _encrypt_table_column(fernet, schema_editor, "diffusion_envois")


def refuse_reverse(apps, schema_editor) -> None:
    # Pas de vrai "reverse" : redéchiffrer en clair recréerait le trou de
    # sécurité que cette migration ferme. `RunPython.noop` documenterait mal
    # l'intention (on ne l'a pas oublié, on le refuse) — un message explicite
    # est préférable si jamais quelqu'un tente `migrate notifications 0007`.
    raise RuntimeError(
        "0008_encrypt_existing_telephone_data ne peut pas être inversée : redéchiffrer "
        "les numéros de téléphone en clair en base recréerait la vulnérabilité que cette "
        "migration corrige."
    )


class Migration(migrations.Migration):

    dependencies = [
        ("notifications", "0007_encrypt_telephone_field"),
    ]

    operations = [
        migrations.RunPython(encrypt_existing_telephone, refuse_reverse),
    ]
