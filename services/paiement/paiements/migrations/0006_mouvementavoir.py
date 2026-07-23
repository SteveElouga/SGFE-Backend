import uuid

from django.db import migrations, models


class Migration(migrations.Migration):
    dependencies = [
        ("paiements", "0005_avoirabonne"),
    ]

    operations = [
        migrations.CreateModel(
            name="MouvementAvoir",
            fields=[
                ("id", models.UUIDField(default=uuid.uuid4, editable=False, primary_key=True, serialize=False)),
                ("abonne_id", models.CharField(max_length=36)),
                ("montant", models.DecimalField(decimal_places=2, max_digits=12)),
                ("type_mouvement", models.CharField(max_length=20)),
                ("motif", models.CharField(blank=True, default="", max_length=255)),
                ("facture_id", models.CharField(blank=True, default="", max_length=36)),
                ("cree_par", models.CharField(blank=True, default="", max_length=36)),
                ("created_at", models.DateTimeField(auto_now_add=True)),
            ],
            options={
                "db_table": "mouvements_avoir",
                "ordering": ["-created_at"],
            },
        ),
        migrations.AddIndex(
            model_name="mouvementavoir",
            index=models.Index(fields=["abonne_id"], name="mouvements__abonne__idx"),
        ),
    ]
