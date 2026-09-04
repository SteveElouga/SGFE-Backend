# Migration de DONNÉES : fait tourner la clé de chiffrement des PII abonné
# (nom, prenom, telephone_whatsapp, adresse) vers une nouvelle valeur de
# `PII_ENCRYPTION_KEY`.
#
# Pourquoi cette migration existe : `docs/CONFORMITE_SOC2_OWASP.md` (§3.1 A02)
# a trouvé que l'ancienne valeur de repli de `PII_ENCRYPTION_KEY` dans
# `docker-compose.yml` était une vraie clé Fernet exploitable, committée en
# clair — pas un placeholder. Cette migration rechiffre les lignes existantes
# (chiffrées avec cette ancienne clé, désormais considérée compromise) avec la
# nouvelle clé posée par `PII_ENCRYPTION_KEY` (voir settings.py, toujours
# fail-fast si absente — inchangé).
#
# Ordre d'exécution attendu (comme pour toute migration de ce projet) :
# `manage.py migrate` tourne AVANT que le service ne commence à servir du
# trafic — donc `settings.PII_ENCRYPTION_KEY` porte déjà la NOUVELLE valeur au
# moment où cette migration s'exécute, alors que les lignes en base sont
# encore chiffrées avec l'ANCIENNE. C'est exactement la fenêtre que cette
# migration comble.
#
# L'ancienne clé est intentionnellement en dur ci-dessous : elle est déjà
# publique (committée en clair dans l'historique Git de `docker-compose.yml`
# avant ce correctif) et son seul rôle ici est de permettre le déchiffrement
# ponctuel des données existantes avant de les rechiffrer avec la nouvelle
# clé — après quoi elle ne sert plus jamais à rien.
#
# Idempotente, sur le même modèle que 0007_encrypt_existing_pii_data : si une
# valeur déchiffre déjà avec la NOUVELLE clé, elle a déjà été migrée, on ne la
# retouche pas. SQL brut (pas l'ORM) pour la même raison qu'en 0007 : le
# modèle historique utilise déjà `EncryptedCharField`, qui déchiffrerait avec
# la nouvelle clé et échouerait sur une ligne encore chiffrée avec l'ancienne.
from __future__ import annotations

from cryptography.fernet import Fernet, InvalidToken
from django.conf import settings
from django.core.exceptions import ImproperlyConfigured
from django.db import migrations

# Ancienne valeur de repli de docker-compose.yml, retirée par ce même
# correctif — voir le commentaire de tête. Ne JAMAIS réutiliser ailleurs.
_ANCIENNE_CLE_COMPROMISE = b"hps7C35W7-d_yhAuUUVePR14wScMSG3ffezJhI0qp58="


def _nouvelle_cle() -> Fernet:
    key = getattr(settings, "PII_ENCRYPTION_KEY", None)
    if not key:
        raise ImproperlyConfigured(
            "PII_ENCRYPTION_KEY est requis pour exécuter la migration "
            "0008_rotate_pii_encryption_key (rotation de la clé de chiffrement PII)."
        )
    return Fernet(key.encode("utf-8") if isinstance(key, str) else key)


def _rechiffrer_si_besoin(nouvelle: Fernet, ancienne: Fernet, value: str | None) -> str | None:
    if value is None or value == "":
        return value
    try:
        nouvelle.decrypt(value.encode("utf-8"))
        return value  # Déjà chiffré avec la nouvelle clé — rien à faire (idempotent).
    except InvalidToken:
        pass
    try:
        clair = ancienne.decrypt(value.encode("utf-8"))
    except InvalidToken:
        # Ne déchiffre avec AUCUNE des deux clés connues : donnée corrompue ou
        # clé inattendue. Échec bruyant plutôt qu'un silence qui laisserait la
        # ligne illisible pour toujours après cette migration.
        raise
    return nouvelle.encrypt(clair).decode("utf-8")


def rotate_pii_key(apps, schema_editor) -> None:
    nouvelle = _nouvelle_cle()
    ancienne = Fernet(_ANCIENNE_CLE_COMPROMISE)
    table = "abonnes"
    with schema_editor.connection.cursor() as cursor:
        cursor.execute(f"SELECT id, nom, prenom, telephone_whatsapp, adresse FROM {table}")
        rows = cursor.fetchall()
        for row_id, nom, prenom, telephone, adresse in rows:
            cursor.execute(
                f"UPDATE {table} SET nom = %s, prenom = %s, telephone_whatsapp = %s, adresse = %s WHERE id = %s",
                [
                    _rechiffrer_si_besoin(nouvelle, ancienne, nom),
                    _rechiffrer_si_besoin(nouvelle, ancienne, prenom),
                    _rechiffrer_si_besoin(nouvelle, ancienne, telephone),
                    _rechiffrer_si_besoin(nouvelle, ancienne, adresse),
                    row_id,
                ],
            )


def refuse_reverse(apps, schema_editor) -> None:
    raise RuntimeError(
        "0008_rotate_pii_encryption_key ne peut pas être inversée : revenir à l'ancienne "
        "clé compromise recréerait la vulnérabilité que cette migration corrige."
    )


class Migration(migrations.Migration):

    dependencies = [
        ("abonnes", "0007_encrypt_existing_pii_data"),
    ]

    operations = [
        migrations.RunPython(rotate_pii_key, refuse_reverse),
    ]
