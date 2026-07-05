"""Tests des vues d'export rapports par campagne (écran 13).

Trois exports back-office (JWT + rôle ADMIN/COMPTABLE) :
    GET /rapports/factures.csv?campagne_id=
    GET /rapports/paiements.csv?campagne_id=
    GET /rapports/synthese/pdf/?campagne_id=
"""

from unittest.mock import Mock, patch

import grpc
from django.test import SimpleTestCase

from schema.grpc_clients import auth_client, facturation_client, paiement_client


class _FakeRpcError(grpc.RpcError):
    def __init__(self, code: grpc.StatusCode) -> None:
        super().__init__()
        self._code = code

    def code(self) -> grpc.StatusCode:
        return self._code


def make_user(role="ADMIN"):
    return Mock(role=role, user_id="u-1", username="jane", is_active=True)


_AUTH = {"HTTP_AUTHORIZATION": "Bearer jwt-valide"}
_CID = "camp-1"


def _facture(numero="FACT-2026-07-0001"):
    return Mock(
        numero_facture=numero,
        abonne_id="ab-1",
        ancien_index=10.0,
        nouveau_index=25.0,
        consommation=15.0,
        prix_m3=500.0,
        montant=7500.0,
        statut="IMPAYEE",
        date_releve="2026-07-01",
        date_limite_paiement="2026-07-06",
    )


def _paiement(pid="pay-1"):
    return Mock(
        paiement_id=pid,
        facture_id="fac-1",
        abonne_id="ab-1",
        montant=7500.0,
        date_paiement="2026-07-03",
        mode_paiement="MOBILE_MONEY",
        reference_transaction="MM-123",
        enregistre_par="u-9",
    )


class FacturesCsvViewTests(SimpleTestCase):
    _URL = "/rapports/factures.csv"

    def test_sans_token_retourne_401(self):
        response = self.client.get(self._URL, {"campagne_id": _CID})
        self.assertEqual(response.status_code, 401)

    def test_role_insuffisant_retourne_403_sans_appel(self):
        with (
            patch.object(auth_client, "validate_token", return_value=make_user(role="AGENT")),
            patch.object(facturation_client, "get_factures_par_campagne") as mock_c,
        ):
            response = self.client.get(self._URL, {"campagne_id": _CID}, **_AUTH)
        self.assertEqual(response.status_code, 403)
        mock_c.assert_not_called()

    def test_campagne_id_manquant_retourne_400(self):
        with patch.object(auth_client, "validate_token", return_value=make_user()):
            response = self.client.get(self._URL, **_AUTH)
        self.assertEqual(response.status_code, 400)

    def test_admin_recupere_le_csv(self):
        with (
            patch.object(auth_client, "validate_token", return_value=make_user(role="ADMIN")),
            patch.object(
                facturation_client,
                "get_factures_par_campagne",
                return_value=Mock(factures=[_facture(), _facture("FACT-2026-07-0002")]),
            ),
        ):
            response = self.client.get(self._URL, {"campagne_id": _CID}, **_AUTH)
        self.assertEqual(response.status_code, 200)
        self.assertIn("text/csv", response["Content-Type"])
        self.assertIn("attachment", response["Content-Disposition"])
        body = response.content.decode("utf-8-sig")
        self.assertIn("numero_facture", body)  # en-tête
        self.assertIn("FACT-2026-07-0001", body)
        self.assertEqual(body.strip().count("\n"), 2)  # 1 en-tête + 2 lignes

    def test_erreur_service_retourne_503(self):
        with (
            patch.object(auth_client, "validate_token", return_value=make_user(role="COMPTABLE")),
            patch.object(
                facturation_client,
                "get_factures_par_campagne",
                side_effect=_FakeRpcError(grpc.StatusCode.INTERNAL),
            ),
        ):
            response = self.client.get(self._URL, {"campagne_id": _CID}, **_AUTH)
        self.assertEqual(response.status_code, 503)


class PaiementsCsvViewTests(SimpleTestCase):
    _URL = "/rapports/paiements.csv"

    def test_sans_token_retourne_401(self):
        response = self.client.get(self._URL, {"campagne_id": _CID})
        self.assertEqual(response.status_code, 401)

    def test_campagne_id_manquant_retourne_400(self):
        with patch.object(auth_client, "validate_token", return_value=make_user()):
            response = self.client.get(self._URL, **_AUTH)
        self.assertEqual(response.status_code, 400)

    def test_comptable_recupere_le_csv(self):
        with (
            patch.object(auth_client, "validate_token", return_value=make_user(role="COMPTABLE")),
            patch.object(
                paiement_client,
                "list_paiements_par_campagne",
                return_value=Mock(paiements=[_paiement(), _paiement("pay-2")]),
            ),
        ):
            response = self.client.get(self._URL, {"campagne_id": _CID}, **_AUTH)
        self.assertEqual(response.status_code, 200)
        self.assertIn("text/csv", response["Content-Type"])
        body = response.content.decode("utf-8-sig")
        self.assertIn("mode_paiement", body)
        self.assertIn("MOBILE_MONEY", body)
        self.assertEqual(body.strip().count("\n"), 2)


class SynthesePdfViewTests(SimpleTestCase):
    _URL = "/rapports/synthese/pdf/"

    def test_sans_token_retourne_401(self):
        response = self.client.get(self._URL, {"campagne_id": _CID})
        self.assertEqual(response.status_code, 401)

    def test_campagne_id_manquant_retourne_400(self):
        with patch.object(auth_client, "validate_token", return_value=make_user()):
            response = self.client.get(self._URL, **_AUTH)
        self.assertEqual(response.status_code, 400)

    def test_admin_recupere_le_pdf(self):
        with (
            patch.object(auth_client, "validate_token", return_value=make_user(role="ADMIN")),
            patch.object(
                facturation_client,
                "generer_synthese_campagne_pdf",
                return_value=Mock(pdf_content=b"%PDF synthese", filename="synthese-camp-1.pdf"),
            ),
        ):
            response = self.client.get(self._URL, {"campagne_id": _CID}, **_AUTH)
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response["Content-Type"], "application/pdf")
        self.assertEqual(b"".join(response.streaming_content), b"%PDF synthese")

    def test_campagne_sans_stats_retourne_404(self):
        with (
            patch.object(auth_client, "validate_token", return_value=make_user(role="ADMIN")),
            patch.object(
                facturation_client,
                "generer_synthese_campagne_pdf",
                side_effect=_FakeRpcError(grpc.StatusCode.NOT_FOUND),
            ),
        ):
            response = self.client.get(self._URL, {"campagne_id": _CID}, **_AUTH)
        self.assertEqual(response.status_code, 404)

    def test_erreur_service_retourne_503(self):
        with (
            patch.object(auth_client, "validate_token", return_value=make_user(role="ADMIN")),
            patch.object(
                facturation_client,
                "generer_synthese_campagne_pdf",
                side_effect=_FakeRpcError(grpc.StatusCode.INTERNAL),
            ),
        ):
            response = self.client.get(self._URL, {"campagne_id": _CID}, **_AUTH)
        self.assertEqual(response.status_code, 503)
