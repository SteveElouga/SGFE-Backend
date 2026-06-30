"""Migration initiale — tables tarifs et factures."""

import uuid

import django.db.models.deletion
from django.db import migrations, models


class Migration(migrations.Migration):
    initial = True
    dependencies: list = []

    operations = [
        migrations.CreateModel(
            name="Tarif",
            fields=[
                ("id", models.UUIDField(default=uuid.uuid4, editable=False, primary_key=True, serialize=False)),
                ("prix_m3", models.DecimalField(decimal_places=2, max_digits=10)),
                ("date_effet", models.DateField()),
                ("is_active", models.BooleanField(default=True)),
                ("created_at", models.DateTimeField(auto_now_add=True)),
            ],
            options={
                "db_table": "tarifs",
                "ordering": ["-created_at"],
            },
        ),
        migrations.CreateModel(
            name="Facture",
            fields=[
                ("id", models.UUIDField(default=uuid.uuid4, editable=False, primary_key=True, serialize=False)),
                ("numero_facture", models.CharField(max_length=30, unique=True)),
                ("abonne_id", models.CharField(max_length=36)),
                ("campagne_id", models.CharField(max_length=36)),
                ("ancien_index", models.DecimalField(decimal_places=3, max_digits=10)),
                ("nouveau_index", models.DecimalField(decimal_places=3, max_digits=10)),
                ("consommation", models.DecimalField(decimal_places=3, max_digits=10)),
                ("prix_m3", models.DecimalField(decimal_places=2, max_digits=10)),
                ("montant", models.DecimalField(decimal_places=2, max_digits=14)),
                (
                    "statut",
                    models.CharField(
                        choices=[
                            ("IMPAYEE", "Impayée"),
                            ("PARTIELLE", "Partiellement payée"),
                            ("PAYEE", "Payée"),
                        ],
                        default="IMPAYEE",
                        max_length=10,
                    ),
                ),
                ("date_releve", models.DateField()),
                ("date_limite_paiement", models.DateField()),
                ("date_generation", models.DateTimeField(auto_now_add=True)),
                ("pdf_path", models.TextField(blank=True, default="")),
            ],
            options={
                "db_table": "factures",
            },
        ),
        migrations.AddIndex(
            model_name="facture",
            index=models.Index(fields=["abonne_id"], name="factures_abonne_id_idx"),
        ),
        migrations.AddIndex(
            model_name="facture",
            index=models.Index(fields=["campagne_id"], name="factures_campagne_id_idx"),
        ),
        migrations.AddIndex(
            model_name="facture",
            index=models.Index(fields=["statut"], name="factures_statut_idx"),
        ),
    ]
