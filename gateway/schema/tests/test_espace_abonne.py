from unittest.mock import Mock, patch

import grpc
from django.test import SimpleTestCase

from proto import facturation_service_pb2 as facturation_pb
from schema.grpc_clients import facturation_client, notification_client, paiement_client


def make_token_response(is_valid=True, abonne_id="abonne-1", date_expiration="2026-08-01"):
    return Mock(is_valid=is_valid, abonne_id=abonne_id, date_expiration=date_expiration)


def make_facture_response(facture_id="facture-1", abonne_id="abonne-1", numero="FACT-2026-07-0001"):
    # Vrai message proto (et non un Mock) : un Mock accepte n'importe quel nom
    # d'attribut et masquerait un décalage de champ (cf. le bug f.numero vs
    # f.numero_facture). Le proto échoue si la vue lit un champ inexistant.
    return facturation_pb.FactureResponse(
        facture_id=facture_id,
        abonne_id=abonne_id,
        numero_facture=numero,
        date_releve="2026-07-01",
        montant=15000.0,
        statut="IMPAYEE",
        date_limite_paiement="2026-07-06",
    )


def make_list_factures_response(*factures):
    return facturation_pb.ListFacturesResponse(factures=list(factures))


def make_pdf_response(pdf_content=b"%PDF-1.4 contenu", filename="facture-1.pdf"):
    return Mock(pdf_content=pdf_content, filename=filename)


class EspaceAbonneListeTests(SimpleTestCase):
    def test_token_invalide_retourne_401(self):
        with patch.object(notification_client, "valider_token", return_value=make_token_response(is_valid=False)):
            response = self.client.get("/espace-abonne/token-invalide/")
        self.assertEqual(response.status_code, 401)

    def test_token_valide_retourne_les_factures_de_labonne(self):
        facture = make_facture_response()
        with (
            patch.object(notification_client, "valider_token", return_value=make_token_response()),
            patch.object(facturation_client, "list_factures", return_value=make_list_factures_response(facture)),
            patch.object(paiement_client, "get_solde", return_value=Mock(solde_restant=15000.0, montant_paye=0.0)),
        ):
            response = self.client.get("/espace-abonne/token-valide/")
        self.assertEqual(response.status_code, 200)
        data = response.json()
        self.assertEqual(data["abonne_id"], "abonne-1")
        self.assertEqual(len(data["factures"]), 1)
        self.assertEqual(data["factures"][0]["facture_id"], "facture-1")


class EspaceAbonnePdfTests(SimpleTestCase):
    """Régression ANO-002 : le PDF d'une facture ne doit être servi qu'à
    l'abonné auquel elle appartient, jamais à un autre abonné authentifié
    par un token qui n'est pas le sien."""

    def test_token_invalide_retourne_401(self):
        with patch.object(notification_client, "valider_token", return_value=make_token_response(is_valid=False)):
            response = self.client.get("/espace-abonne/token-invalide/facture/facture-1/pdf/")
        self.assertEqual(response.status_code, 401)

    def test_facture_dun_autre_abonne_retourne_404(self):
        """IDOR : token valide pour abonne-1, mais la facture demandée appartient à abonne-2."""
        facture_dun_tiers = make_facture_response(facture_id="facture-99", abonne_id="abonne-2")
        with (
            patch.object(notification_client, "valider_token", return_value=make_token_response(abonne_id="abonne-1")),
            patch.object(facturation_client, "get_facture", return_value=facture_dun_tiers),
            patch.object(facturation_client, "get_facture_pdf") as mock_get_pdf,
        ):
            response = self.client.get("/espace-abonne/token-abonne-1/facture/facture-99/pdf/")
        self.assertEqual(response.status_code, 404)
        mock_get_pdf.assert_not_called()

    def test_facture_de_son_propre_abonne_retourne_le_pdf(self):
        sa_facture = make_facture_response(facture_id="facture-1", abonne_id="abonne-1")
        with (
            patch.object(notification_client, "valider_token", return_value=make_token_response(abonne_id="abonne-1")),
            patch.object(facturation_client, "get_facture", return_value=sa_facture),
            patch.object(facturation_client, "get_facture_pdf", return_value=make_pdf_response()),
        ):
            response = self.client.get("/espace-abonne/token-abonne-1/facture/facture-1/pdf/")
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response["Content-Type"], "application/pdf")

    def test_facture_introuvable_retourne_404(self):
        with (
            patch.object(notification_client, "valider_token", return_value=make_token_response()),
            patch.object(facturation_client, "get_facture", side_effect=grpc.RpcError("introuvable")),
        ):
            response = self.client.get("/espace-abonne/token-valide/facture/inconnue/pdf/")
        self.assertEqual(response.status_code, 404)
