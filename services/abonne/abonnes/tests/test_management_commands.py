import json
import os
import tempfile
from io import StringIO
from typing import Any
from unittest.mock import Mock, patch

from django.core.management import CommandError, call_command
from django.test import TestCase

from abonnes.services import AbonneService


def _create_abonne(**overrides: Any) -> Any:
    defaults: dict[str, Any] = dict(
        nom="Doe",
        prenom="John",
        telephone_whatsapp="+24100000000",
        adresse="Quartier X",
        numero_compteur=1,
        quartier="Centre",
        camp=1,
        index_initial=0,
        date_pose="2024-01-01",
    )
    defaults.update(overrides)
    return AbonneService().create_abonne(**defaults)


def _mocked_clients() -> tuple[Any, Any, Any, Any]:
    """Patches des 4 clients gRPC sortants — la commande ne doit dépendre
    d'aucun service externe réellement démarré pour être testée."""
    return (
        patch("abonnes.export.CampagneServiceClient", return_value=Mock(list_releves_abonne=Mock(return_value=[]))),
        patch("abonnes.export.FacturationServiceClient", return_value=Mock(list_factures_abonne=Mock(return_value=[]))),
        patch("abonnes.export.PaiementServiceClient", return_value=Mock(list_paiements_abonne=Mock(return_value=[]))),
        patch(
            "abonnes.export.NotificationServiceClient",
            return_value=Mock(list_envois_abonne=Mock(return_value=[])),
        ),
    )


class ExporterDonneesAbonneCommandTests(TestCase):
    def test_exporte_sur_stdout_par_defaut(self) -> None:
        abonne = _create_abonne()
        out = StringIO()
        p1, p2, p3, p4 = _mocked_clients()
        with p1, p2, p3, p4:
            call_command("exporter_donnees_abonne", str(abonne.id), stdout=out)

        donnees = json.loads(out.getvalue())
        self.assertEqual(donnees["abonne_id"], str(abonne.id))
        self.assertEqual(donnees["identite"]["nom"], "Doe")

    def test_exporte_vers_un_fichier_avec_output(self) -> None:
        abonne = _create_abonne()
        out = StringIO()
        p1, p2, p3, p4 = _mocked_clients()
        with tempfile.TemporaryDirectory() as tmp:
            chemin = os.path.join(tmp, "export.json")
            with p1, p2, p3, p4:
                call_command("exporter_donnees_abonne", str(abonne.id), "--output", chemin, stdout=out)

            self.assertTrue(os.path.exists(chemin))
            with open(chemin, encoding="utf-8") as fichier:
                donnees = json.load(fichier)
            self.assertEqual(donnees["abonne_id"], str(abonne.id))
        self.assertIn("écrit dans", out.getvalue())

    def test_abonne_introuvable_leve_command_error(self) -> None:
        out = StringIO()
        p1, p2, p3, p4 = _mocked_clients()
        with p1, p2, p3, p4, self.assertRaises(CommandError):
            call_command("exporter_donnees_abonne", "00000000-0000-0000-0000-000000000000", stdout=out)
