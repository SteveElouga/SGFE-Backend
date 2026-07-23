from django.db import migrations, models


class Migration(migrations.Migration):
    dependencies = [
        ("paiements", "0004_paiement_annulation"),
    ]

    operations = [
        migrations.CreateModel(
            name="AvoirAbonne",
            fields=[
                ("abonne_id", models.CharField(max_length=36, primary_key=True, serialize=False)),
                ("montant", models.DecimalField(decimal_places=2, default=0, max_digits=12)),
                ("updated_at", models.DateTimeField(auto_now=True)),
            ],
            options={
                "db_table": "avoirs_abonnes",
            },
        ),
    ]
