"""Migration initiale du Paiement Service — création des tables Paiement, SoldeFacture et SuiviImpaye."""

import uuid

from django.db import migrations, models


class Migration(migrations.Migration):
    initial = True

    dependencies: list = []

    operations = [
        migrations.CreateModel(
            name="Paiement",
            fields=[
                (
                    "id",
                    models.UUIDField(
                        default=uuid.uuid4,
                        editable=False,
                        primary_key=True,
                        serialize=False,
                    ),
                ),
                ("facture_id", models.CharField(max_length=36)),
                ("abonne_id", models.CharField(max_length=36)),
                ("montant", models.DecimalField(decimal_places=2, max_digits=12)),
                ("date_paiement", models.DateField()),
                (
                    "mode_paiement",
                    models.CharField(
                        choices=[
                            ("ESPECES", "Espèces"),
                            ("MOBILE_MONEY", "Mobile Money"),
                            ("VIREMENT", "Virement bancaire"),
                        ],
                        max_length=20,
                    ),
                ),
                (
                    "reference_transaction",
                    models.CharField(blank=True, default="", max_length=100),
                ),
                ("enregistre_par", models.CharField(max_length=36)),
                ("created_at", models.DateTimeField(auto_now_add=True)),
            ],
            options={
                "db_table": "paiements",
                "ordering": ["-created_at"],
            },
        ),
        migrations.AddIndex(
            model_name="paiement",
            index=models.Index(fields=["facture_id"], name="paiements_facture_id_idx"),
        ),
        migrations.CreateModel(
            name="SoldeFacture",
            fields=[
                (
                    "facture_id",
                    models.CharField(max_length=36, primary_key=True, serialize=False),
                ),
                ("abonne_id", models.CharField(max_length=36)),
                ("montant_total", models.DecimalField(decimal_places=2, max_digits=12)),
                (
                    "montant_paye",
                    models.DecimalField(decimal_places=2, default=0, max_digits=12),
                ),
                ("solde_restant", models.DecimalField(decimal_places=2, max_digits=12)),
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
                ("date_limite_paiement", models.DateField()),
                ("updated_at", models.DateTimeField(auto_now=True)),
            ],
            options={
                "db_table": "soldes_factures",
            },
        ),
        migrations.CreateModel(
            name="SuiviImpaye",
            fields=[
                (
                    "id",
                    models.UUIDField(
                        default=uuid.uuid4,
                        editable=False,
                        primary_key=True,
                        serialize=False,
                    ),
                ),
                ("facture_id", models.CharField(max_length=36, unique=True)),
                ("abonne_id", models.CharField(max_length=36)),
                ("date_depassement", models.DateField()),
                ("etape_actuelle", models.IntegerField(default=1)),
                ("rappel_1_envoye", models.BooleanField(default=False)),
                ("date_rappel_1", models.DateTimeField(blank=True, null=True)),
                ("rappel_2_envoye", models.BooleanField(default=False)),
                ("date_rappel_2", models.DateTimeField(blank=True, null=True)),
                ("avertissement_envoye", models.BooleanField(default=False)),
                ("date_avertissement", models.DateTimeField(blank=True, null=True)),
                ("suspension_effectuee", models.BooleanField(default=False)),
                ("date_suspension", models.DateTimeField(blank=True, null=True)),
                ("relances_suspendues_jusqu", models.DateField(blank=True, null=True)),
                ("resolu_le", models.DateField(blank=True, null=True)),
                ("created_at", models.DateTimeField(auto_now_add=True)),
            ],
            options={
                "db_table": "suivis_impayes",
            },
        ),
    ]
