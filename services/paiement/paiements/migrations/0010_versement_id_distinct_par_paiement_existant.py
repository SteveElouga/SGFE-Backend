# Rattrapage de 0009.
#
# `AddField(default=uuid.uuid4)` n'évalue le défaut **qu'une fois** : toutes les
# lignes existantes reçoivent la MÊME valeur. Mesuré après 0009 sur la base de
# développement : 16 paiements, 1 seul `versement_id`.
#
# Or `annuler_paiement` annule tout le versement. Sans ce rattrapage, annuler un
# seul de ces paiements aurait annulé les seize — en rétablissant seize soldes.
#
# Chaque écriture antérieure à la cascade a été créée seule : elle forme donc
# son propre versement, et c'est ce que cette migration écrit.

import uuid

from django.db import migrations


def un_versement_par_paiement(apps, schema_editor):
    Paiement = apps.get_model("paiements", "Paiement")
    for paiement in Paiement.objects.all().iterator():
        Paiement.objects.filter(pk=paiement.pk).update(versement_id=uuid.uuid4())


def retour_arriere(apps, schema_editor):
    """Irréversible sans perte de sens.

    Regrouper à nouveau ces écritures sous un identifiant commun recréerait
    exactement le défaut qu'on corrige. On laisse la colonne telle quelle : le
    retour arrière de 0009 la supprimera de toute façon.
    """


class Migration(migrations.Migration):
    dependencies = [
        ("paiements", "0009_paiement_versement_id_and_more"),
    ]

    operations = [
        migrations.RunPython(un_versement_par_paiement, retour_arriere),
    ]
