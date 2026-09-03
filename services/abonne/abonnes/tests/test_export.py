from typing import Any
from unittest.mock import Mock

import grpc
from django.test import TestCase

from abonnes.export import ExportService, exporter_donnees_abonne, exporter_donnees_abonne_json
from abonnes.models import Abonne
from abonnes.services import AbonneService, CompteurService


def _create_abonne(**overrides: Any) -> Abonne:
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


def _service_ok(**retours: Any) -> ExportService:
    """ExportService dont les 4 clients externes répondent normalement."""
    return ExportService(
        campagne_client=Mock(list_releves_abonne=Mock(return_value=retours.get("releves", []))),
        facturation_client=Mock(list_factures_abonne=Mock(return_value=retours.get("factures", []))),
        paiement_client=Mock(list_paiements_abonne=Mock(return_value=retours.get("paiements", []))),
        notification_client=Mock(list_envois_abonne=Mock(return_value=retours.get("envois", []))),
    )


class ExportServiceIdentiteTests(TestCase):
    def test_export_contient_identite_dechiffree(self) -> None:
        abonne = _create_abonne()
        service = _service_ok()
        export = service.exporter(str(abonne.id))
        self.assertEqual(export["abonne_id"], str(abonne.id))
        self.assertEqual(export["identite"]["numero_abonne"], "AB-0001")
        self.assertEqual(export["identite"]["nom"], "Doe")
        self.assertEqual(export["identite"]["prenom"], "John")
        self.assertEqual(export["identite"]["telephone_whatsapp"], "+24100000000")
        self.assertEqual(export["identite"]["adresse"], "Quartier X")
        self.assertEqual(export["identite"]["statut"], "ACTIF")

    def test_export_abonne_inconnu_leve_une_exception(self) -> None:
        from django.core.exceptions import ObjectDoesNotExist

        service = _service_ok()
        with self.assertRaises(ObjectDoesNotExist):
            service.exporter("00000000-0000-0000-0000-000000000000")

    def test_export_contient_le_compteur_actif(self) -> None:
        abonne = _create_abonne()
        service = _service_ok()
        export = service.exporter(str(abonne.id))
        self.assertIsNotNone(export["compteurs"]["actif"])
        self.assertEqual(export["compteurs"]["actif"]["numero_compteur"], 1)
        self.assertEqual(export["compteurs"]["historique_remplacements"], [])

    def test_export_contient_l_historique_de_remplacement(self) -> None:
        abonne = _create_abonne()
        CompteurService().remplacer_compteur(
            abonne_id=str(abonne.id),
            index_fermeture=100,
            nouveau_numero_compteur=2,
            nouveau_quartier="Q2",
            nouveau_camp=2,
            nouvel_index_initial=0,
            date_remplacement="2024-06-01",
            motif="Compteur défectueux",
        )
        service = _service_ok()
        export = service.exporter(str(abonne.id))
        historique = export["compteurs"]["historique_remplacements"]
        self.assertEqual(len(historique), 1)
        self.assertEqual(historique[0]["motif"], "Compteur défectueux")


class ExportServiceSectionsExternesTests(TestCase):
    def test_toutes_les_sections_disponibles_quand_tout_repond(self) -> None:
        abonne = _create_abonne()
        service = _service_ok(
            releves=[{"releve_id": "r1"}],
            factures=[{"facture_id": "f1"}],
            paiements=[{"paiement_id": "p1"}],
            envois=[{"envoi_id": "e1"}],
        )
        export = service.exporter(str(abonne.id))
        for section in ("releves", "factures", "paiements", "envois_whatsapp"):
            with self.subTest(section=section):
                self.assertTrue(export[section]["disponible"])
                self.assertEqual(len(export[section]["donnees"]), 1)

    def test_diffusions_whatsapp_toujours_documentee_comme_non_exposee(self) -> None:
        abonne = _create_abonne()
        export = _service_ok().exporter(str(abonne.id))
        self.assertFalse(export["diffusions_whatsapp"]["disponible"])
        self.assertIn("raison", export["diffusions_whatsapp"])

    def test_section_releves_degrade_gracieusement_si_campagne_indisponible(self) -> None:
        abonne = _create_abonne()
        service = ExportService(
            campagne_client=Mock(list_releves_abonne=Mock(side_effect=grpc.RpcError("indisponible"))),
            facturation_client=Mock(list_factures_abonne=Mock(return_value=[])),
            paiement_client=Mock(list_paiements_abonne=Mock(return_value=[])),
            notification_client=Mock(list_envois_abonne=Mock(return_value=[])),
        )
        export = service.exporter(str(abonne.id))
        self.assertFalse(export["releves"]["disponible"])
        self.assertIn("raison", export["releves"])
        # Le reste de l'export n'est PAS impacté par la panne d'un seul service.
        self.assertTrue(export["factures"]["disponible"])
        self.assertTrue(export["paiements"]["disponible"])
        self.assertTrue(export["envois_whatsapp"]["disponible"])
        self.assertEqual(export["identite"]["nom"], "Doe")

    def test_plusieurs_sections_indisponibles_simultanement(self) -> None:
        abonne = _create_abonne()
        service = ExportService(
            campagne_client=Mock(list_releves_abonne=Mock(side_effect=grpc.RpcError("indisponible"))),
            facturation_client=Mock(list_factures_abonne=Mock(side_effect=grpc.RpcError("indisponible"))),
            paiement_client=Mock(list_paiements_abonne=Mock(return_value=[])),
            notification_client=Mock(list_envois_abonne=Mock(return_value=[])),
        )
        export = service.exporter(str(abonne.id))
        self.assertFalse(export["releves"]["disponible"])
        self.assertFalse(export["factures"]["disponible"])
        self.assertTrue(export["paiements"]["disponible"])


class ExporterDonneesAbonneFonctionsTests(TestCase):
    def test_exporter_donnees_abonne_fonction_deleque_a_export_service(self) -> None:
        abonne = _create_abonne()
        export = exporter_donnees_abonne(
            str(abonne.id),
            campagne_client=Mock(list_releves_abonne=Mock(return_value=[])),
            facturation_client=Mock(list_factures_abonne=Mock(return_value=[])),
            paiement_client=Mock(list_paiements_abonne=Mock(return_value=[])),
            notification_client=Mock(list_envois_abonne=Mock(return_value=[])),
        )
        self.assertEqual(export["abonne_id"], str(abonne.id))

    def test_exporter_donnees_abonne_json_produit_du_json_valide_et_lisible(self) -> None:
        import json

        abonne = _create_abonne()
        payload = exporter_donnees_abonne_json(
            str(abonne.id),
            campagne_client=Mock(list_releves_abonne=Mock(return_value=[])),
            facturation_client=Mock(list_factures_abonne=Mock(return_value=[])),
            paiement_client=Mock(list_paiements_abonne=Mock(return_value=[])),
            notification_client=Mock(list_envois_abonne=Mock(return_value=[])),
        )
        self.assertIn("\n", payload)  # indenté, pas une seule ligne compacte
        donnees = json.loads(payload)
        self.assertEqual(donnees["identite"]["nom"], "Doe")
