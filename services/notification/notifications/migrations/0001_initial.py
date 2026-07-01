"""Migration initiale — Notification Service.

Créée manuellement (pas de PostgreSQL local).
Crée les tables envois et tokens_acces.
"""

import uuid

import django.db.models.deletion
import django.utils.timezone
from django.db import migrations, models


class Migration(migrations.Migration):

    initial = True

    dependencies: list = []

    operations = [
        migrations.CreateModel(
            name="Envoi",
            fields=[
                ("id", models.UUIDField(default=uuid.uuid4, editable=False, primary_key=True, serialize=False)),
                ("facture_id", models.CharField(max_length=36)),
                ("abonne_id", models.CharField(max_length=36)),
                (
                    "type_envoi",
                    models.CharField(
                        choices=[
                            ("FACTURE", "Facture"),
                            ("RELANCE_1", "Relance étape 1"),
                            ("RELANCE_2", "Relance étape 2"),
                            ("AVERTISSEMENT", "Avertissement"),
                            ("SUSPENSION", "Suspension"),
                            ("RETABLISSEMENT", "Rétablissement"),
                        ],
                        max_length=30,
                    ),
                ),
                ("telephone", models.CharField(max_length=20)),
                (
                    "statut",
                    models.CharField(
                        choices=[
                            ("EN_ATTENTE", "En attente"),
                            ("ENVOYE", "Envoyé"),
                            ("ECHEC", "Échec"),
                        ],
                        default="EN_ATTENTE",
                        max_length=20,
                    ),
                ),
                ("date_envoi", models.DateTimeField(blank=True, null=True)),
                ("telnyx_message_id", models.CharField(blank=True, default="", max_length=100)),
                ("erreur", models.TextField(blank=True, default="")),
                ("tentatives", models.IntegerField(default=0)),
                ("created_at", models.DateTimeField(auto_now_add=True)),
            ],
            options={
                "db_table": "envois",
            },
        ),
        migrations.CreateModel(
            name="TokenAcces",
            fields=[
                ("id", models.UUIDField(default=uuid.uuid4, editable=False, primary_key=True, serialize=False)),
                ("abonne_id", models.CharField(max_length=36)),
                ("facture_id", models.CharField(max_length=36)),
                ("token", models.UUIDField(default=uuid.uuid4, unique=True)),
                ("date_expiration", models.DateField()),
                ("date_derniere_visite", models.DateTimeField(blank=True, null=True)),
                ("is_active", models.BooleanField(default=True)),
                ("created_at", models.DateTimeField(auto_now_add=True)),
            ],
            options={
                "db_table": "tokens_acces",
            },
        ),
        migrations.AddIndex(
            model_name="envoi",
            index=models.Index(fields=["facture_id"], name="envois_facture_id_idx"),
        ),
        migrations.AddIndex(
            model_name="envoi",
            index=models.Index(fields=["abonne_id"], name="envois_abonne_id_idx"),
        ),
        migrations.AddIndex(
            model_name="tokenacces",
            index=models.Index(fields=["token"], name="tokens_acces_token_idx"),
        ),
        migrations.AddIndex(
            model_name="tokenacces",
            index=models.Index(fields=["abonne_id"], name="tokens_acces_abonne_id_idx"),
        ),
    ]
