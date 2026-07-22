from django.db import migrations, models


class Migration(migrations.Migration):
    dependencies = [
        ("paiements", "0003_paiement_unique_reference_transaction_non_vide"),
    ]

    operations = [
        migrations.AddField(
            model_name="paiement",
            name="annule",
            field=models.BooleanField(default=False),
        ),
        migrations.AddField(
            model_name="paiement",
            name="annule_le",
            field=models.DateTimeField(blank=True, null=True),
        ),
        migrations.AddField(
            model_name="paiement",
            name="annule_par",
            field=models.CharField(blank=True, default="", max_length=36),
        ),
        migrations.AddField(
            model_name="paiement",
            name="motif_annulation",
            field=models.CharField(blank=True, default="", max_length=255),
        ),
    ]
