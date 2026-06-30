import uuid
import django.db.models.deletion
from django.conf import settings
from django.db import migrations, models


class Migration(migrations.Migration):
    dependencies = [
        ("comptes", "0002_alter_user_role_passwordsetuptoken"),
    ]

    operations = [
        # email devient nullable (non requis pour les rôles non-ADMIN)
        migrations.AlterField(
            model_name="user",
            name="email",
            field=models.EmailField(blank=True, max_length=255, null=True, unique=True),
        ),
        # numéro de téléphone camerounais, obligatoire pour tous les rôles
        # null=True permet aux lignes existantes de recevoir NULL (pas de violation unique)
        # la contrainte NOT NULL sera appliquée par la validation Django, pas la BDD
        migrations.AddField(
            model_name="user",
            name="phone_number",
            field=models.CharField(max_length=20, unique=True, null=True, blank=True),
        ),
        # table OTP pour activation et reset par WhatsApp
        migrations.CreateModel(
            name="PhoneOtpToken",
            fields=[
                ("id", models.UUIDField(default=uuid.uuid4, editable=False, primary_key=True, serialize=False)),
                ("otp_hash", models.CharField(max_length=255)),
                ("created_at", models.DateTimeField(auto_now_add=True)),
                ("expires_at", models.DateTimeField()),
                ("used_at", models.DateTimeField(blank=True, null=True)),
                (
                    "user",
                    models.ForeignKey(
                        on_delete=django.db.models.deletion.CASCADE,
                        related_name="phone_otp_tokens",
                        to=settings.AUTH_USER_MODEL,
                    ),
                ),
            ],
            options={
                "db_table": "phone_otp_tokens",
            },
        ),
    ]
