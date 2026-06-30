import uuid

import django.db.models.deletion
from django.db import migrations, models


class Migration(migrations.Migration):
    initial = True

    dependencies: list = []

    operations = [
        migrations.CreateModel(
            name="Campagne",
            fields=[
                ("id", models.UUIDField(default=uuid.uuid4, editable=False, primary_key=True, serialize=False)),
                ("nom", models.CharField(max_length=200)),
                ("periode_mois", models.IntegerField()),
                ("periode_annee", models.IntegerField()),
                (
                    "statut",
                    models.CharField(
                        choices=[
                            ("PLANIFIEE", "Planifiée"),
                            ("EN_COURS", "En cours"),
                            ("CLOTUREE", "Clôturée"),
                        ],
                        default="PLANIFIEE",
                        max_length=20,
                    ),
                ),
                ("date_planifiee", models.DateField(blank=True, null=True)),
                ("created_by", models.CharField(max_length=36)),
                ("date_creation", models.DateTimeField(auto_now_add=True)),
                ("date_cloture", models.DateTimeField(blank=True, null=True)),
            ],
            options={
                "db_table": "campagnes",
                "ordering": ["-date_creation"],
            },
        ),
        migrations.CreateModel(
            name="Releve",
            fields=[
                ("id", models.UUIDField(default=uuid.uuid4, editable=False, primary_key=True, serialize=False)),
                (
                    "campagne",
                    models.ForeignKey(
                        on_delete=django.db.models.deletion.CASCADE,
                        related_name="releves",
                        to="campagnes.campagne",
                    ),
                ),
                ("abonne_id", models.CharField(max_length=36)),
                ("ancien_index", models.FloatField()),
                ("nouveau_index", models.FloatField(blank=True, null=True)),
                ("consommation", models.FloatField(blank=True, null=True)),
                ("date_releve", models.DateTimeField(blank=True, null=True)),
                ("observation", models.TextField(blank=True, default="")),
                (
                    "statut",
                    models.CharField(
                        choices=[
                            ("A_RELEVER", "À relever"),
                            ("RELEVE", "Relevé"),
                            ("NON_RELEVE", "Non relevé"),
                            ("ESTIME", "Estimé"),
                        ],
                        default="A_RELEVER",
                        max_length=20,
                    ),
                ),
                ("agent_id", models.CharField(blank=True, default="", max_length=36)),
            ],
            options={
                "db_table": "releves",
            },
        ),
        migrations.AlterUniqueTogether(
            name="releve",
            unique_together={("campagne", "abonne_id")},
        ),
    ]
