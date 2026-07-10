from django.db import migrations, models


class Migration(migrations.Migration):
    dependencies = [
        ("comptes", "0004_phoneotptoken_attempts"),
    ]

    operations = [
        # Aligne le schéma sur le modèle : phone_number est obligatoire pour tous
        # les rôles (activation/reset par OTP WhatsApp), donc non-nullable. En
        # 0003 le champ avait été introduit en null=True uniquement pour ne pas
        # violer la contrainte sur d'éventuelles lignes existantes.
        migrations.AlterField(
            model_name="user",
            name="phone_number",
            field=models.CharField(max_length=20, unique=True),
        ),
    ]
