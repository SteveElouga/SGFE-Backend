from unittest.mock import Mock, patch

import grpc
from django.test import SimpleTestCase

from schema.grpc_clients import auth_client, facturation_client


class _FakeRpcError(grpc.RpcError):
    """RpcError exposant `.code()` comme les vraies erreurs gRPC (`_InactiveRpcError`).

    Une `grpc.RpcError` nue n'a pas de `.code()` ; la vue en a besoin pour
    distinguer NOT_FOUND (404) du reste (503).
    """

    def __init__(self, code: grpc.StatusCode) -> None:
        super().__init__()
        self._code = code

    def code(self) -> grpc.StatusCode:
        return self._code


def make_user(role="ADMIN"):
    return Mock(role=role, user_id="u-1", username="jane", is_active=True)


def make_pdf_response(pdf_content=b"%PDF-1.4 contenu", filename="facture-1.pdf"):
    return Mock(pdf_content=pdf_content, filename=filename)


_URL = "/factures/facture-1/pdf/"
_AUTH = {"HTTP_AUTHORIZATION": "Bearer jwt-valide"}


class FacturePdfViewTests(SimpleTestCase):
    """Vue back-office GET /factures/<id>/pdf/ (JWT + rôle ADMIN/COMPTABLE)."""

    def test_sans_token_retourne_401(self):
        response = self.client.get(_URL)
        self.assertEqual(response.status_code, 401)

    def test_token_invalide_retourne_401(self):
        with patch.object(auth_client, "validate_token", side_effect=grpc.RpcError("invalide")):
            response = self.client.get(_URL, **_AUTH)
        self.assertEqual(response.status_code, 401)

    def test_role_insuffisant_retourne_403_sans_appeler_le_pdf(self):
        with (
            patch.object(auth_client, "validate_token", return_value=make_user(role="AGENT")),
            patch.object(facturation_client, "get_facture_pdf") as mock_pdf,
        ):
            response = self.client.get(_URL, **_AUTH)
        self.assertEqual(response.status_code, 403)
        mock_pdf.assert_not_called()

    def test_admin_recupere_le_pdf(self):
        with (
            patch.object(auth_client, "validate_token", return_value=make_user(role="ADMIN")),
            patch.object(facturation_client, "get_facture_pdf", return_value=make_pdf_response()),
        ):
            response = self.client.get(_URL, **_AUTH)
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response["Content-Type"], "application/pdf")
        self.assertEqual(b"".join(response.streaming_content), b"%PDF-1.4 contenu")

    def test_comptable_autorise(self):
        with (
            patch.object(auth_client, "validate_token", return_value=make_user(role="COMPTABLE")),
            patch.object(facturation_client, "get_facture_pdf", return_value=make_pdf_response()),
        ):
            response = self.client.get(_URL, **_AUTH)
        self.assertEqual(response.status_code, 200)

    def test_facture_introuvable_retourne_404(self):
        with (
            patch.object(auth_client, "validate_token", return_value=make_user(role="ADMIN")),
            patch.object(
                facturation_client,
                "get_facture_pdf",
                side_effect=_FakeRpcError(grpc.StatusCode.NOT_FOUND),
            ),
        ):
            response = self.client.get(_URL, **_AUTH)
        self.assertEqual(response.status_code, 404)

    def test_erreur_service_retourne_503(self):
        with (
            patch.object(auth_client, "validate_token", return_value=make_user(role="ADMIN")),
            patch.object(
                facturation_client,
                "get_facture_pdf",
                side_effect=_FakeRpcError(grpc.StatusCode.INTERNAL),
            ),
        ):
            response = self.client.get(_URL, **_AUTH)
        self.assertEqual(response.status_code, 503)
