"""Tests des vues d'export (écran 13).

Trois exports back-office (JWT + rôle ADMIN/COMPTABLE) :
    GET /rapports/factures.csv   ?campagne_id= et/ou ?date_debut=&date_fin=
    GET /rapports/paiements.csv  ?campagne_id= et/ou ?date_debut=&date_fin=
    GET /rapports/synthese/pdf/  ?campagne_id=

`campagne_id` était obligatoire sur les deux CSV : aucun journal par période
n'était possible, et les régularisations — créées avec `campagne_id` vide —
étaient exportables par aucun chemin.
"""

from typing import Any
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


def make_user(role: str = "ADMIN") -> Mock:
    return Mock(role=role, user_id="u-1", username="jane", is_active=True)


_AUTH: dict[str, Any] = {"HTTP_AUTHORIZATION": "Bearer jwt-valide"}
_CID = "camp-1"


def _facture(
    numero: str = "FACT-2026-07-0001", nature: str = "CONSOMMATION", motif: str = "", campagne_id: str = _CID
) -> Mock:
    return Mock(
        numero_facture=numero,
        nature=nature,
        motif=motif,
        campagne_id=campagne_id,
        date_generation="2026-07-01T08:00:00+00:00",
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


def _paiement(pid: str = "pay-1", annule: bool = False) -> Mock:
    return Mock(
        paiement_id=pid,
        facture_id="fac-1",
        abonne_id="ab-1",
        montant=7500.0,
        date_paiement="2026-07-03",
        mode_paiement="MOBILE_MONEY",
        reference_transaction="MM-123",
        enregistre_par="u-9",
        annule=annule,
        annule_le="2026-07-05" if annule else "",
        annule_par="jane" if annule else "",
        motif_annulation="erreur de saisie" if annule else "",
    )


class FacturesCsvViewTests(SimpleTestCase):
    _URL = "/rapports/factures.csv"

    def test_sans_token_retourne_401(self) -> None:
        response = self.client.get(self._URL, {"campagne_id": _CID})
        self.assertEqual(response.status_code, 401)

    def test_role_insuffisant_retourne_403_sans_appel(self) -> None:
        with (
            patch.object(auth_client, "validate_token", return_value=make_user(role="AGENT")),
            patch.object(facturation_client, "get_factures_par_campagne") as mock_c,
        ):
            response = self.client.get(self._URL, {"campagne_id": _CID}, **_AUTH)
        self.assertEqual(response.status_code, 403)
        mock_c.assert_not_called()

    def test_sans_critere_exporte_tout(self) -> None:
        """Une clôture d'exercice demande tout l'historique.

        Le paramètre était obligatoire et la vue rendait 400. Ce refus était le
        premier des deux verrous qui rendaient une clôture comptable infaisable
        sans ressaisie.
        """
        with (
            patch.object(auth_client, "validate_token", return_value=make_user()),
            patch.object(facturation_client, "list_factures", return_value=Mock(factures=[_facture()])) as mock_l,
        ):
            response = self.client.get(self._URL, **_AUTH)
        self.assertEqual(response.status_code, 200)
        self.assertEqual(mock_l.call_args.kwargs, {"campagne_id": "", "date_debut": "", "date_fin": ""})

    def test_periode_transmise_au_service(self) -> None:
        with (
            patch.object(auth_client, "validate_token", return_value=make_user()),
            patch.object(facturation_client, "list_factures", return_value=Mock(factures=[])) as mock_l,
        ):
            response = self.client.get(self._URL, {"date_debut": "2026-07-01", "date_fin": "2026-07-31"}, **_AUTH)
        self.assertEqual(response.status_code, 200)
        self.assertEqual(mock_l.call_args.kwargs["date_debut"], "2026-07-01")
        self.assertEqual(mock_l.call_args.kwargs["date_fin"], "2026-07-31")
        self.assertIn("2026-07-01_2026-07-31", response["Content-Disposition"])

    def test_date_illisible_refusee(self) -> None:
        """Ignorer la borne rendrait tout l'historique sans le dire."""
        with patch.object(auth_client, "validate_token", return_value=make_user()):
            response = self.client.get(self._URL, {"date_debut": "01/07/2026"}, **_AUTH)
        self.assertEqual(response.status_code, 400)
        self.assertIn("AAAA-MM-JJ", response.json()["erreur"])

    def test_bornes_inversees_refusees(self) -> None:
        with patch.object(auth_client, "validate_token", return_value=make_user()):
            response = self.client.get(self._URL, {"date_debut": "2026-07-31", "date_fin": "2026-07-01"}, **_AUTH)
        self.assertEqual(response.status_code, 400)

    def test_une_regularisation_apparait_avec_sa_nature_et_son_motif(self) -> None:
        """Elle n'apparaissait dans AUCUN export.

        Créée avec `campagne_id=""`, le filtre par campagne ne la trouvait jamais.
        C'est pourtant la seule dette qu'on saisit à la main : l'arriéré antérieur
        à la mise en service. Et sans la colonne `nature`, rien ne la distinguait
        dans le fichier d'une facture de consommation à 0 m³.
        """
        reg = _facture("REG-2026-07-0001", nature="REGULARISATION", motif="Arriéré 2025", campagne_id="")
        with (
            patch.object(auth_client, "validate_token", return_value=make_user()),
            patch.object(facturation_client, "list_factures", return_value=Mock(factures=[reg])),
        ):
            response = self.client.get(self._URL, {"date_debut": "2026-07-01"}, **_AUTH)
        body = response.content.decode("utf-8-sig")
        self.assertIn("REG-2026-07-0001", body)
        self.assertIn("REGULARISATION", body)
        self.assertIn("Arriéré 2025", body)

    def test_admin_recupere_le_csv(self) -> None:
        with (
            patch.object(auth_client, "validate_token", return_value=make_user(role="ADMIN")),
            patch.object(
                facturation_client,
                "list_factures",
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

    def test_erreur_service_retourne_503(self) -> None:
        with (
            patch.object(auth_client, "validate_token", return_value=make_user(role="COMPTABLE")),
            patch.object(
                facturation_client,
                "list_factures",
                side_effect=_FakeRpcError(grpc.StatusCode.INTERNAL),
            ),
        ):
            response = self.client.get(self._URL, {"campagne_id": _CID}, **_AUTH)
        self.assertEqual(response.status_code, 503)


class PaiementsCsvViewTests(SimpleTestCase):
    _URL = "/rapports/paiements.csv"

    def test_sans_token_retourne_401(self) -> None:
        response = self.client.get(self._URL, {"campagne_id": _CID})
        self.assertEqual(response.status_code, 401)

    def test_periode_passe_par_le_filtre_de_dates_et_non_par_campagne(self) -> None:
        """`ListPaiementsParCampagne` filtre `SoldeFacture.campagne_id`.

        Un paiement de régularisation a un `campagne_id` vide : ce chemin ne
        pouvait donc jamais le trouver. Le filtre par date porte sur
        `Paiement.date_paiement` et voit tous les versements.
        """
        with (
            patch.object(auth_client, "validate_token", return_value=make_user()),
            patch.object(paiement_client, "list_paiements", return_value=Mock(paiements=[_paiement()])) as mock_p,
            patch.object(paiement_client, "list_paiements_par_campagne") as mock_c,
        ):
            response = self.client.get(self._URL, {"date_debut": "2026-07-01"}, **_AUTH)
        self.assertEqual(response.status_code, 200)
        mock_c.assert_not_called()
        self.assertEqual(mock_p.call_args.kwargs["date_debut"], "2026-07-01")

    def test_les_paiements_annules_sont_signales(self) -> None:
        """Ils étaient DÉJÀ dans l'export, sans rien qui les signale.

        Un comptable qui sommait la colonne `montant` comptait donc comme
        recette des versements annulés — faux, et faux en silence.
        """
        with (
            patch.object(auth_client, "validate_token", return_value=make_user()),
            patch.object(
                paiement_client,
                "list_paiements",
                return_value=Mock(paiements=[_paiement("pay-1"), _paiement("pay-2", annule=True)]),
            ),
        ):
            response = self.client.get(self._URL, {"date_debut": "2026-07-01"}, **_AUTH)
        body = response.content.decode("utf-8-sig")
        self.assertIn("annule", body.splitlines()[0])
        self.assertIn("erreur de saisie", body)
        lignes = body.strip().splitlines()
        self.assertEqual(len([ligne for ligne in lignes[1:] if ";OUI;" in ligne]), 1)

    def test_comptable_recupere_le_csv(self) -> None:
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

    def test_sans_token_retourne_401(self) -> None:
        response = self.client.get(self._URL, {"campagne_id": _CID})
        self.assertEqual(response.status_code, 401)

    def test_campagne_id_manquant_retourne_400(self) -> None:
        with patch.object(auth_client, "validate_token", return_value=make_user()):
            response = self.client.get(self._URL, **_AUTH)
        self.assertEqual(response.status_code, 400)

    def test_admin_recupere_le_pdf(self) -> None:
        with (
            patch.object(auth_client, "validate_token", return_value=make_user(role="ADMIN")),
            patch.object(
                facturation_client,
                "generer_synthese_campagne_pdf",
                return_value=Mock(pdf_content=b"%PDF synthese", filename="synthese-camp-1.pdf"),
            ),
        ):
            response: Any = self.client.get(self._URL, {"campagne_id": _CID}, **_AUTH)
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response["Content-Type"], "application/pdf")
        self.assertEqual(b"".join(response.streaming_content), b"%PDF synthese")

    def test_campagne_sans_stats_retourne_404(self) -> None:
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

    def test_erreur_service_retourne_503(self) -> None:
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


class RecuPaiementPdfViewTests(SimpleTestCase):
    _URL = "/paiements/pay-1/recu/pdf/"
    _FID = {"facture_id": "fac-1"}

    def test_sans_token_retourne_401(self) -> None:
        response = self.client.get(self._URL, self._FID)
        self.assertEqual(response.status_code, 401)

    def test_role_insuffisant_retourne_403(self) -> None:
        with patch.object(auth_client, "validate_token", return_value=make_user(role="AGENT")):
            response = self.client.get(self._URL, self._FID, **_AUTH)
        self.assertEqual(response.status_code, 403)

    def test_facture_id_manquant_retourne_400(self) -> None:
        with patch.object(auth_client, "validate_token", return_value=make_user()):
            response = self.client.get(self._URL, **_AUTH)
        self.assertEqual(response.status_code, 400)

    def _paiement(self, paiement_id: str = "pay-1", abonne_id: str = "ab-1", montant: float = 10750.0) -> Mock:
        return Mock(paiement_id=paiement_id, abonne_id=abonne_id, montant=montant)

    def test_comptable_recupere_le_pdf(self) -> None:
        with (
            patch.object(auth_client, "validate_token", return_value=make_user(role="COMPTABLE")),
            patch.object(
                paiement_client,
                "list_paiements",
                return_value=Mock(paiements=[self._paiement()]),
            ),
            patch.object(
                paiement_client,
                "get_dette_abonne",
                return_value=Mock(total_du=10500.0),
            ) as mock_dette,
            patch.object(
                facturation_client,
                "generer_recu_paiement_pdf",
                return_value=Mock(pdf_content=b"%PDF recu", filename="REC-2026-06-0002-1.pdf"),
            ) as mock_gen,
        ):
            response: Any = self.client.get(self._URL, self._FID, **_AUTH)
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response["Content-Type"], "application/pdf")
        self.assertEqual(b"".join(response.streaming_content), b"%PDF recu")
        # Le montant du versement et la dette totale de l'abonné doivent être
        # transmis — sans eux, Facturation retombe sur les défauts protobuf à 0
        # et le reçu annonce inconditionnellement « plus rien n'est dû ».
        mock_dette.assert_called_once_with("ab-1")
        mock_gen.assert_called_once_with("pay-1", "fac-1", montant_versement=10750.0, solde_restant_total=10500.0)

    def test_paiement_introuvable_retourne_404(self) -> None:
        with (
            patch.object(auth_client, "validate_token", return_value=make_user(role="ADMIN")),
            patch.object(paiement_client, "list_paiements", return_value=Mock(paiements=[])),
        ):
            response = self.client.get(self._URL, self._FID, **_AUTH)
        self.assertEqual(response.status_code, 404)

    def test_erreur_service_retourne_503(self) -> None:
        with (
            patch.object(auth_client, "validate_token", return_value=make_user(role="ADMIN")),
            patch.object(
                paiement_client,
                "list_paiements",
                return_value=Mock(paiements=[self._paiement()]),
            ),
            patch.object(
                paiement_client,
                "get_dette_abonne",
                return_value=Mock(total_du=10500.0),
            ),
            patch.object(
                facturation_client,
                "generer_recu_paiement_pdf",
                side_effect=_FakeRpcError(grpc.StatusCode.INTERNAL),
            ),
        ):
            response = self.client.get(self._URL, self._FID, **_AUTH)
        self.assertEqual(response.status_code, 503)

    def test_service_paiement_indisponible_retourne_503(self) -> None:
        with (
            patch.object(auth_client, "validate_token", return_value=make_user(role="ADMIN")),
            patch.object(
                paiement_client,
                "list_paiements",
                side_effect=_FakeRpcError(grpc.StatusCode.UNAVAILABLE),
            ),
        ):
            response = self.client.get(self._URL, self._FID, **_AUTH)
        self.assertEqual(response.status_code, 503)
