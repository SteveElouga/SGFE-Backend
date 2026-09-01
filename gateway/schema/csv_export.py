"""Écriture d'un CSV lisible par Excel en français.

Extrait de `rapports_views.py` le jour où l'espace abonné a eu besoin du même
service. Deux écritures CSV dans le même dépôt finissent par divergre sur le
séparateur ou sur le BOM — et l'une des deux s'ouvre alors en une seule colonne
chez l'utilisateur, sans que personne ne comprenne pourquoi l'autre marche.
"""

import csv
import io

from django.http import HttpResponse


def csv_response(filename: str, header: list[str], rows: list[list[object]]) -> HttpResponse:
    """Sérialise des lignes en CSV UTF-8 en pièce jointe.

    Deux choix qui ne se devinent pas :

    * **séparateur `;`** — Excel en configuration française l'attend par défaut ;
      avec une virgule, tout le fichier arrive dans une seule colonne ;
    * **BOM UTF-8** — sans lui, Excel lit les accents en latin-1 et affiche
      « FactÃ»re ».
    """
    buffer = io.StringIO()
    writer = csv.writer(buffer, delimiter=";")
    writer.writerow(header)
    writer.writerows(rows)
    content = "﻿" + buffer.getvalue()
    response = HttpResponse(content, content_type="text/csv; charset=utf-8")
    response["Content-Disposition"] = f'attachment; filename="{filename}"'
    return response
