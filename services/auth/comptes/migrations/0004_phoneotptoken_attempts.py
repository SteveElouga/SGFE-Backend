from django.db import migrations, models


class Migration(migrations.Migration):
    dependencies = [
        ("comptes", "0003_user_phone_number_phoneotptoken"),
    ]

    operations = [
        # Compteur d'échecs par OTP : borne le brute-force du code à 6 chiffres.
        migrations.AddField(
            model_name="phoneotptoken",
            name="attempts",
            field=models.IntegerField(default=0),
        ),
    ]
