import sys
from pathlib import Path
from typing import Any
from unittest.mock import Mock, patch

import grpc
from django.conf import settings
from django.test import SimpleTestCase

# Import non qualifié par le paquet `proto` (comme schema/grpc_clients.py) :
# mypy.ini n'exclut de la vérification que le nom de module nu
# `facturation_service_pb2`, pas sa forme qualifiée — voir test_stats.py.
sys.path.insert(0, str(Path(settings.BASE_DIR) / "proto"))

import facturation_service_pb2 as facturation_pb  # noqa: E402

from schema.grpc_clients import facturation_client, notification_client, paiement_client  # noqa: E402


def make_token_response(
    is_valid: bool = True, abonne_id: str = "abonne-1", date_expiration: str = "2026-08-01"
) -> Mock:
    return Mock(is_valid=is_valid, abonne_id=abonne_id, date_expiration=date_expiration)


def make_facture_response(
    facture_id: str = "facture-1", abonne_id: str = "abonne-1", numero: str = "FACT-2026-07-0001"
) -> facturation_pb.FactureResponse:
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


def make_list_factures_response(*factures: facturation_pb.FactureResponse) -> facturation_pb.ListFacturesResponse:
    return facturation_pb.ListFacturesResponse(factures=list(factures))


def make_pdf_response(pdf_content: bytes = b"%PDF-1.4 contenu", filename: str = "facture-1.pdf") -> Mock:
    return Mock(pdf_content=pdf_content, filename=filename)


class EspaceAbonneListeTests(SimpleTestCase):
    def test_token_invalide_retourne_401(self) -> None:
        with patch.object(notification_client, "valider_token", return_value=make_token_response(is_valid=False)):
            response = self.client.get("/espace-abonne/token-invalide/")
        self.assertEqual(response.status_code, 401)

    def test_token_valide_retourne_les_factures_de_labonne(self) -> None:
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

    def test_token_invalide_retourne_401(self) -> None:
        with patch.object(notification_client, "valider_token", return_value=make_token_response(is_valid=False)):
            response = self.client.get("/espace-abonne/token-invalide/facture/facture-1/pdf/")
        self.assertEqual(response.status_code, 401)

    def test_facture_dun_autre_abonne_retourne_404(self) -> None:
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

    def test_facture_de_son_propre_abonne_retourne_le_pdf(self) -> None:
        sa_facture = make_facture_response(facture_id="facture-1", abonne_id="abonne-1")
        with (
            patch.object(notification_client, "valider_token", return_value=make_token_response(abonne_id="abonne-1")),
            patch.object(facturation_client, "get_facture", return_value=sa_facture),
            patch.object(facturation_client, "get_facture_pdf", return_value=make_pdf_response()),
        ):
            response = self.client.get("/espace-abonne/token-abonne-1/facture/facture-1/pdf/")
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response["Content-Type"], "application/pdf")

    def test_facture_introuvable_retourne_404(self) -> None:
        with (
            patch.object(notification_client, "valider_token", return_value=make_token_response()),
            patch.object(facturation_client, "get_facture", side_effect=grpc.RpcError("introuvable")),
        ):
            response = self.client.get("/espace-abonne/token-valide/facture/inconnue/pdf/")
        self.assertEqual(response.status_code, 404)


def make_facture_consommation(
    facture_id: str = "facture-conso",
    numero: str = "FACT-2026-07-0002",
    ancien: float = 1240.0,
    nouveau: float = 1283.0,
    conso: float = 43.0,
    prix: float = 500.0,
) -> facturation_pb.FactureResponse:
    """Une facture de consommation, index compris — ce que l'abonné doit pouvoir vérifier."""
    return facturation_pb.FactureResponse(
        facture_id=facture_id,
        abonne_id="abonne-1",
        numero_facture=numero,
        ancien_index=ancien,
        nouveau_index=nouveau,
        consommation=conso,
        prix_m3=prix,
        montant=conso * prix,
        statut="IMPAYEE",
        date_releve="2026-07-01",
        date_limite_paiement="2026-07-06",
        nature="CONSOMMATION",
    )


class EspaceAbonneConsommationTests(SimpleTestCase):
    """L'abonné voit ses mètres cubes, pas seulement des montants.

    EF-NOTIF-003 demande un « historique de consommation », §8.3 du SRS le
    redemande. Le payload ne portait ni index ni consommation : l'abonné lisait
    un montant qu'il n'avait aucun moyen de vérifier. Les quatre champs étaient
    dans `FactureResponse` depuis toujours — personne ne les recopiait.
    """

    def _get(self, facture: facturation_pb.FactureResponse) -> Any:
        with (
            patch.object(notification_client, "valider_token", return_value=make_token_response()),
            patch.object(facturation_client, "list_factures", return_value=make_list_factures_response(facture)),
            patch.object(paiement_client, "get_solde", return_value=Mock(solde_restant=21500.0, montant_paye=0.0)),
            patch.object(paiement_client, "get_avoir_abonne", return_value=Mock(montant=0.0)),
        ):
            return self.client.get("/espace-abonne/token-valide/")

    def test_les_index_et_la_consommation_sont_dans_le_payload(self) -> None:
        data = self._get(make_facture_consommation()).json()
        f = data["factures"][0]
        self.assertEqual(f["ancien_index"], 1240.0)
        self.assertEqual(f["nouveau_index"], 1283.0)
        self.assertEqual(f["consommation"], 43.0)
        self.assertEqual(f["prix_m3"], 500.0)

    def test_le_montant_se_verifie_depuis_ce_que_le_payload_porte(self) -> None:
        """C'est tout l'intérêt : conso × prix doit redonner le montant.

        Sans ces champs, l'abonné ne pouvait que croire le chiffre.
        """
        f = self._get(make_facture_consommation()).json()["factures"][0]
        self.assertAlmostEqual(f["consommation"] * f["prix_m3"], f["montant"])
        self.assertAlmostEqual(f["nouveau_index"] - f["ancien_index"], f["consommation"])

    def test_une_regularisation_porte_des_index_nuls_et_son_motif(self) -> None:
        """Elle n'a pas de relevé — ce sont `nature` et `motif` qui expliquent
        le montant à la place des index."""
        reg = facturation_pb.FactureResponse(
            facture_id="facture-reg",
            abonne_id="abonne-1",
            numero_facture="REG-2026-07-0001",
            montant=12000.0,
            statut="IMPAYEE",
            date_releve="2026-07-20",
            date_limite_paiement="2026-07-25",
            nature="REGULARISATION",
            motif="Arriéré antérieur à la mise en service",
        )
        f = self._get(reg).json()["factures"][0]
        self.assertEqual(f["consommation"], 0.0)
        self.assertEqual(f["nature"], "REGULARISATION")
        self.assertEqual(f["motif"], "Arriéré antérieur à la mise en service")


class EspaceAbonneCsvTests(SimpleTestCase):
    """Le relevé de compte en CSV — promis deux fois par le SRS, absent.

    Seuls la vue JSON et le PDF d'UNE facture existaient : l'abonné pouvait
    télécharger une facture à la fois, jamais l'état de son compte.
    """

    _URL = "/espace-abonne/token-valide/factures.csv"

    def test_token_invalide_retourne_401(self) -> None:
        with patch.object(notification_client, "valider_token", return_value=make_token_response(is_valid=False)):
            response = self.client.get("/espace-abonne/token-invalide/factures.csv")
        self.assertEqual(response.status_code, 401)

    def _csv(self, *factures: facturation_pb.FactureResponse) -> Any:
        with (
            patch.object(notification_client, "valider_token", return_value=make_token_response()),
            patch.object(facturation_client, "list_factures", return_value=make_list_factures_response(*factures)),
            patch.object(paiement_client, "get_solde", return_value=Mock(solde_restant=21500.0, montant_paye=0.0)),
            patch.object(paiement_client, "get_avoir_abonne", return_value=Mock(montant=0.0)),
        ):
            return self.client.get(self._URL)

    def test_le_csv_porte_la_consommation_et_les_index(self) -> None:
        response = self._csv(make_facture_consommation())
        self.assertEqual(response.status_code, 200)
        self.assertIn("text/csv", response["Content-Type"])
        corps = response.content.decode("utf-8-sig")
        entete = corps.splitlines()[0]
        for colonne in ("ancien_index", "nouveau_index", "consommation_m3", "prix_m3", "solde_restant"):
            self.assertIn(colonne, entete)
        self.assertIn("1240.0;1283.0;43.0;500.0", corps)

    def test_le_separateur_et_le_bom_sont_ceux_qu_excel_attend(self) -> None:
        """Séparateur `;` et BOM UTF-8 : sans eux, le fichier arrive en une
        colonne ou avec les accents cassés."""
        response = self._csv(make_facture_consommation())
        self.assertTrue(response.content.startswith(b"\xef\xbb\xbf"))
        self.assertIn(b";", response.content)

    def test_le_nom_du_fichier_ne_contient_pas_le_token(self) -> None:
        """Un fichier reste dans un dossier de téléchargements ; un token dans
        un nom de fichier est un identifiant d'accès qui traîne."""
        response = self._csv(make_facture_consommation())
        disposition = response["Content-Disposition"]
        self.assertIn("attachment", disposition)
        self.assertNotIn("token-valide", disposition)
        self.assertIn("mon-compte-", disposition)

    def test_une_ligne_par_facture(self) -> None:
        response = self._csv(make_facture_consommation(), make_facture_consommation("f2", "FACT-2026-08-0003"))
        lignes = response.content.decode("utf-8-sig").strip().splitlines()
        self.assertEqual(len(lignes), 3)  # en-tête + 2
