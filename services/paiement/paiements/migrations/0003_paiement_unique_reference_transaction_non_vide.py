from django.db import migrations, models


class Migration(migrations.Migration):
    dependencies = [
        ("paiements", "0002_rename_paiements_facture_id_idx_paiements_facture_c032a3_idx_and_more"),
    ]

    operations = [
        migrations.AddConstraint(
            model_name="paiement",
            constraint=models.UniqueConstraint(
                condition=models.Q(("reference_transaction", ""), _negated=True),
                fields=("reference_transaction",),
                name="unique_reference_transaction_non_vide",
            ),
        ),
    ]
