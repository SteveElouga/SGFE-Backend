# Durcit `email_hash`/`phone_number_hash` une fois les données existantes
# backfillées par 0012_encrypt_existing_email_phone_data :
#   - `phone_number_hash` devient non-nullable (comme `phone_number`
#     lui-même, obligatoire pour tous les rôles) ;
#   - les deux colonnes reçoivent `unique=True` — c'est ELLES qui portent
#     désormais la contrainte d'unicité auparavant posée sur `email`/
#     `phone_number` en clair (voir comptes/fields.py, tête de module, pour
#     la justification : Fernet est un chiffrement non déterministe, une
#     contrainte d'unicité sur le champ chiffré lui-même ne protégerait plus
#     rien).
#
# Posée dans une migration séparée (ni 0011 ni 0012) : ajouter `unique=True`
# avant que 0012 n'ait rempli ces colonnes pour les lignes existantes aurait
# tenté de créer l'index unique sur des colonnes encore à moitié NULL — sans
# risque de collision réelle (NULL est toujours distinct de NULL), mais
# `phone_number_hash` doit de toute façon perdre son `null=True` transitoire
# avant de pouvoir porter la contrainte `unique=True` définitive visée par le
# modèle. Même séquencement en trois temps que celui déjà appliqué à
# `phone_number` lui-même entre 0003_user_phone_number_phoneotptoken (ajouté
# nullable) et 0005_alter_user_phone_number (durci en non-nullable).

from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ('comptes', '0012_encrypt_existing_email_phone_data'),
    ]

    operations = [
        migrations.AlterField(
            model_name='user',
            name='email_hash',
            field=models.CharField(blank=True, db_index=True, editable=False, max_length=64, null=True, unique=True),
        ),
        migrations.AlterField(
            model_name='user',
            name='phone_number_hash',
            field=models.CharField(db_index=True, editable=False, max_length=64, unique=True),
        ),
    ]
