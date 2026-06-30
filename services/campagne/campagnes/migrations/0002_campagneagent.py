import uuid

import django.db.models.deletion
from django.db import migrations, models


class Migration(migrations.Migration):
    dependencies = [
        ("campagnes", "0001_initial"),
    ]

    operations = [
        migrations.CreateModel(
            name="CampagneAgent",
            fields=[
                ("id", models.UUIDField(default=uuid.uuid4, editable=False, primary_key=True, serialize=False)),
                (
                    "campagne",
                    models.ForeignKey(
                        on_delete=django.db.models.deletion.CASCADE,
                        related_name="agents_affectes",
                        to="campagnes.campagne",
                    ),
                ),
                ("agent_id", models.CharField(max_length=36)),
                ("date_affectation", models.DateTimeField(auto_now_add=True)),
            ],
            options={
                "db_table": "campagne_agents",
            },
        ),
        migrations.AlterUniqueTogether(
            name="campagneagent",
            unique_together={("campagne", "agent_id")},
        ),
    ]
