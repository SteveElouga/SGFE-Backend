"""Tests de l'export RGPD (comptes/export.py) — équivalent, côté Auth
Service, de abonnes/tests/test_export.py (PR #179)."""

import json
from unittest.mock import patch

from django.test import TestCase
from django.utils import timezone

from comptes.export import ExportService, exporter_donnees_utilisateur, exporter_donnees_utilisateur_json
from comptes.models import Role, User
from comptes.services import UserAdminService


def _create_user(**overrides: object) -> User:
    defaults: dict[str, object] = dict(
        username="agentx",
        email="agentx@example.com",
        password="S3cr3t!",
        role=Role.AGENT,
        phone_number="+237690000090",
    )
    defaults.update(overrides)
    return User.objects.create_user(**defaults)  # type: ignore[arg-type]


class ExportServiceIdentiteTests(TestCase):
    def test_export_contient_identite(self) -> None:
        user = _create_user()
        export = ExportService().exporter(str(user.id))
        self.assertEqual(export["user_id"], str(user.id))
        self.assertTrue(export["identite"]["disponible"])
        self.assertEqual(export["identite"]["donnees"]["username"], "agentx")
        self.assertEqual(export["identite"]["donnees"]["email"], "agentx@example.com")
        self.assertEqual(export["identite"]["donnees"]["telephone"], "+237690000090")
        self.assertEqual(export["identite"]["donnees"]["role"], "AGENT")

    def test_export_email_absent_est_none(self) -> None:
        user = _create_user(username="agenty", email=None, phone_number="+237690000091")
        export = ExportService().exporter(str(user.id))
        self.assertIsNone(export["identite"]["donnees"]["email"])

    def test_export_utilisateur_inconnu_leve_une_exception(self) -> None:
        from django.core.exceptions import ObjectDoesNotExist

        with self.assertRaises(ObjectDoesNotExist):
            ExportService().exporter("00000000-0000-0000-0000-000000000000")

    def test_export_contient_les_dates_de_compte(self) -> None:
        user = _create_user(username="agentz", phone_number="+237690000092")
        export = ExportService().exporter(str(user.id))
        self.assertTrue(export["compte"]["disponible"])
        self.assertIsNotNone(export["compte"]["donnees"]["date_creation"])
        self.assertIsNone(export["compte"]["donnees"]["date_desactivation"])
        self.assertTrue(export["compte"]["donnees"]["actif"])

    def test_export_contient_la_date_de_desactivation_si_desactive(self) -> None:
        user = User.objects.create_user(
            username="agenta1", phone_number="+237690000093", role=Role.AGENT, password="S3cr3t!"
        )
        UserAdminService().deactivate_user(str(user.id))
        export = ExportService().exporter(str(user.id))
        self.assertIsNotNone(export["compte"]["donnees"]["date_desactivation"])
        self.assertFalse(export["compte"]["donnees"]["actif"])


class ExportServiceHistoriqueConnexionTests(TestCase):
    def test_historique_connexion_documente_absence_de_last_login(self) -> None:
        """last_login n'est pas alimenté par AuthService.login aujourd'hui —
        l'export le documente honnêtement plutôt que de l'omettre."""
        user = _create_user(username="agentb1", phone_number="+237690000094")
        export = ExportService().exporter(str(user.id))
        self.assertTrue(export["historique_connexion"]["disponible"])
        self.assertIsNone(export["historique_connexion"]["donnees"]["derniere_connexion"])
        self.assertIn("note", export["historique_connexion"]["donnees"])

    def test_historique_connexion_expose_last_login_si_renseigne(self) -> None:
        user = _create_user(username="agentb2", phone_number="+237690000095")
        user.last_login = timezone.now()
        user.save(update_fields=["last_login"])
        export = ExportService().exporter(str(user.id))
        self.assertIsNotNone(export["historique_connexion"]["donnees"]["derniere_connexion"])
        self.assertNotIn("note", export["historique_connexion"]["donnees"])


class ExportServiceDegradationGracieuseTests(TestCase):
    """Une section qui échoue n'empêche jamais les autres — même contrat que
    abonnes/export.py::ExportService._section."""

    def test_section_en_echec_ne_bloque_pas_les_autres(self) -> None:
        user = _create_user(username="agentc1", phone_number="+237690000096")
        with patch.object(ExportService, "_donnees_compte", side_effect=RuntimeError("boom")):
            export = ExportService().exporter(str(user.id))

        self.assertFalse(export["compte"]["disponible"])
        self.assertIn("raison", export["compte"])
        # Le reste de l'export n'est PAS impacté par la panne d'une seule section.
        self.assertTrue(export["identite"]["disponible"])
        self.assertTrue(export["historique_connexion"]["disponible"])
        self.assertEqual(export["identite"]["donnees"]["username"], "agentc1")

    def test_plusieurs_sections_en_echec_simultanement(self) -> None:
        user = _create_user(username="agentc2", phone_number="+237690000097")
        with (
            patch.object(ExportService, "_donnees_compte", side_effect=RuntimeError("boom compte")),
            patch.object(ExportService, "_donnees_historique_connexion", side_effect=RuntimeError("boom histo")),
        ):
            export = ExportService().exporter(str(user.id))

        self.assertFalse(export["compte"]["disponible"])
        self.assertFalse(export["historique_connexion"]["disponible"])
        self.assertTrue(export["identite"]["disponible"])

    def test_identite_en_echec_ne_bloque_pas_le_reste(self) -> None:
        user = _create_user(username="agentc3", phone_number="+237690000098")
        with patch.object(ExportService, "_donnees_identite", side_effect=RuntimeError("boom identite")):
            export = ExportService().exporter(str(user.id))

        self.assertFalse(export["identite"]["disponible"])
        self.assertTrue(export["compte"]["disponible"])
        self.assertTrue(export["historique_connexion"]["disponible"])


class ExporterDonneesUtilisateurFonctionsTests(TestCase):
    def test_exporter_donnees_utilisateur_fonction_deleque_a_export_service(self) -> None:
        user = _create_user(username="agentd1", phone_number="+237690000099")
        export = exporter_donnees_utilisateur(str(user.id))
        self.assertEqual(export["user_id"], str(user.id))

    def test_exporter_donnees_utilisateur_json_produit_du_json_valide_et_lisible(self) -> None:
        user = _create_user(username="agentd2", phone_number="+237690000100")
        payload = exporter_donnees_utilisateur_json(str(user.id))
        self.assertIn("\n", payload)  # indenté, pas une seule ligne compacte
        donnees = json.loads(payload)
        self.assertEqual(donnees["identite"]["donnees"]["username"], "agentd2")
