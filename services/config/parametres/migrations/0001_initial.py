import uuid

import django.db.models.deletion
from django.db import migrations, models


class Migration(migrations.Migration):
    initial = True

    dependencies = []

    operations = [
        migrations.CreateModel(
            name="InfosSociete",
            fields=[
                ("id", models.AutoField(auto_created=True, primary_key=True, serialize=False, verbose_name="ID")),
                ("nom", models.CharField(default="", max_length=200)),
                ("adresse", models.TextField(default="")),
                ("telephone", models.CharField(default="", max_length=20)),
                ("logo_path", models.CharField(blank=True, default="", max_length=500)),
                ("updated_at", models.DateTimeField(auto_now=True)),
            ],
            options={"db_table": "infos_societe"},
        ),
        migrations.CreateModel(
            name="ConfigParam",
            fields=[
                ("id", models.UUIDField(default=uuid.uuid4, editable=False, primary_key=True, serialize=False)),
                ("cle", models.CharField(max_length=100, unique=True)),
                ("valeur", models.TextField()),
                ("description", models.TextField(blank=True, default="")),
                ("updated_at", models.DateTimeField(auto_now=True)),
            ],
            options={"db_table": "config_params", "ordering": ["cle"]},
        ),
    ]
