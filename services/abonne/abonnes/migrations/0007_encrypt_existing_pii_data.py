# Migration de DONNÉES : rechiffre les valeurs existantes de la table
# `abonnes` (nom, prenom, telephone_whatsapp, adresse) après le changement de
# schéma de 0006_encrypt_pii_fields.
#
# Pourquoi du SQL brut plutôt que l'ORM (`apps.get_model`) : à ce point de
# l'historique des migrations, le modèle historique reconstruit par Django
# utilise déjà `EncryptedCharField`/`EncryptedTextField` (0006 est déjà
# appliquée) — lire `abonne.nom` via l'ORM tenterait donc de DÉCHIFFRER une
# valeur qui est encore en clair, et lèverait `InvalidToken` sur la toute
# première ligne. Cette migration lit/écrit directement la colonne, en
# contournant `from_db_value`/`get_prep_value`.
#
# Idempotente : si une valeur est déjà un token Fernet valide (relance de la
# migration, ou table déjà chiffrée), elle n'est pas rechiffrée une seconde
# fois — un Fernet re-chiffré n'est de toute façon pas un bug de sécurité,
# mais autant éviter le travail inutile et rester prévisible.
#
# Contexte dépôt (voir MEMORY.md) : les données de démo ont été purgées le
# 2026-08-27 et aucun seed n'a été relancé depuis — la table `abonnes` de cet
# environnement de dev est donc probablement vide au moment où cette
# migration s'exécute ici (0 ligne affectée). Elle reste néanmoins écrite
# pour être correcte sur une base non vide (env. d'un autre développeur, ou
# production) : ne JAMAIS supposer une base vierge dans une migration.
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
            "0007_encrypt_existing_pii_data (rechiffrement des PII abonné existantes)."
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


def encrypt_existing_pii(apps, schema_editor) -> None:
    fernet = _build_fernet()
    table = "abonnes"
    with schema_editor.connection.cursor() as cursor:
        cursor.execute(f"SELECT id, nom, prenom, telephone_whatsapp, adresse FROM {table}")
        rows = cursor.fetchall()
        for row_id, nom, prenom, telephone, adresse in rows:
            cursor.execute(
                f"UPDATE {table} SET nom = %s, prenom = %s, telephone_whatsapp = %s, adresse = %s WHERE id = %s",
                [
                    _encrypt_if_needed(fernet, nom),
                    _encrypt_if_needed(fernet, prenom),
                    _encrypt_if_needed(fernet, telephone),
                    _encrypt_if_needed(fernet, adresse),
                    row_id,
                ],
            )


def refuse_reverse(apps, schema_editor) -> None:
    # Pas de vrai "reverse" : redéchiffrer en clair recréerait le trou de
    # sécurité que cette migration ferme. `RunPython.noop` documenterait mal
    # l'intention (on ne l'a pas oublié, on le refuse) — un message explicite
    # est préférable si jamais quelqu'un tente `migrate abonnes 0006`.
    raise RuntimeError(
        "0007_encrypt_existing_pii_data ne peut pas être inversée : redéchiffrer les PII "
        "abonné en clair en base recréerait la vulnérabilité que cette migration corrige."
    )


class Migration(migrations.Migration):

    dependencies = [
        ("abonnes", "0006_encrypt_pii_fields"),
    ]

    operations = [
        migrations.RunPython(encrypt_existing_pii, refuse_reverse),
    ]
