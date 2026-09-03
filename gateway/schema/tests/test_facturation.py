"""Tests des resolvers GraphQL du Facturation Service (gateway).

Régression ANO-022 : aucun test n'existait pour ce domaine.
"""

from unittest.mock import MagicMock, patch

from django.test import SimpleTestCase

from schema.facturation_mutations import FacturationMutations
from schema.facturation_queries import FacturationQueries


def _facture_response(**kwargs) -> MagicMock:
    defaults = dict(
        facture_id="facture-001",
        numero_facture="FACT-2026-07-0001",
        abonne_id="abonne-001",
        campagne_id="camp-001",
        ancien_index=100.0,
        nouveau_index=150.0,
        consommation=50.0,
        prix_m3=500.0,
        montant=25000.0,
        statut="IMPAYEE",
        date_releve="2026-07-01",
        date_limite_paiement="2026-07-06",
        date_generation="2026-07-01T10:00:00",
        pdf_path="/pdfs/FACT-2026-07-0001.pdf",
        numero_mobile_money="",
    )
    defaults.update(kwargs)
    return MagicMock(**defaults)


def _tarif_response(**kwargs) -> MagicMock:
    defaults = dict(tarif_id="tarif-001", prix_m3=500.0, date_effet="2026-01-01", is_active=True)
    defaults.update(kwargs)
    return MagicMock(**defaults)


class TestFacturationQueries(SimpleTestCase):
    @patch("schema.facturation_queries.facturation_client")
    @patch("schema.facturation_queries.require_auth")
    @patch("schema.facturation_queries.require_role")
    def test_tarif_actuel_admin(self, mock_role, mock_auth, mock_client) -> None:
        mock_auth.return_value = MagicMock(role="ADMIN")
        mock_client.get_tarif_actuel.return_value = _tarif_response()
        info = MagicMock()
        result = FacturationQueries().tarif_actuel(info)
        self.assertEqual(result.prix_m3, 500.0)
        self.assertTrue(result.is_active)

    @patch("schema.facturation_queries.facturation_client")
    @patch("schema.facturation_queries.require_auth")
    @patch("schema.facturation_queries.require_role")
    def test_facture_par_id(self, mock_role, mock_auth, mock_client) -> None:
        mock_auth.return_value = MagicMock(role="COMPTABLE")
        mock_client.get_facture.return_value = _facture_response()
        info = MagicMock()
        result = FacturationQueries().facture(info, facture_id="facture-001")
        self.assertEqual(result.facture_id, "facture-001")
        self.assertEqual(result.montant, 25000.0)

    @patch("schema.facturation_queries.facturation_client")
    @patch("schema.facturation_queries.require_auth")
    @patch("schema.facturation_queries.require_role")
    def test_factures_avec_filtres(self, mock_role, mock_auth, mock_client) -> None:
        mock_auth.return_value = MagicMock(role="ADMIN")
        mock_client.list_factures.return_value = MagicMock(
            factures=[_facture_response(), _facture_response(facture_id="facture-002")]
        )
        info = MagicMock()
        result = FacturationQueries().factures(info, campagne_id="camp-001", statut="IMPAYEE")
        self.assertEqual(len(result), 2)
        mock_client.list_factures.assert_called_once_with(campagne_id="camp-001", abonne_id="", statut="IMPAYEE")

    @patch("schema.facturation_queries.facturation_client")
    @patch("schema.facturation_queries.require_auth")
    @patch("schema.facturation_queries.require_role")
    def test_factures_sans_limit_offset_appelle_le_client_comme_avant(self, mock_role, mock_auth, mock_client) -> None:
        """Non-régression explicite : `limit`/`offset` omis, l'appel au client
        gRPC reste identique à ce qu'il était avant leur introduction."""
        mock_auth.return_value = MagicMock(role="ADMIN")
        mock_client.list_factures.return_value = MagicMock(factures=[_facture_response()])
        info = MagicMock()
        result = FacturationQueries().factures(info)
        self.assertEqual(len(result), 1)
        mock_client.list_factures.assert_called_once_with(campagne_id="", abonne_id="", statut="")

    @patch("schema.facturation_queries.facturation_client")
    @patch("schema.facturation_queries.require_auth")
    @patch("schema.facturation_queries.require_role")
    def test_factures_avec_pagination_transmet_limit_offset(self, mock_role, mock_auth, mock_client) -> None:
        mock_auth.return_value = MagicMock(role="ADMIN")
        mock_client.list_factures.return_value = MagicMock(factures=[])
        info = MagicMock()
        FacturationQueries().factures(info, campagne_id="camp-001", limit=10, offset=5)
        mock_client.list_factures.assert_called_once_with(
            campagne_id="camp-001", abonne_id="", statut="", limit=10, offset=5
        )

    @patch("schema.facturation_queries.facturation_client")
    @patch("schema.facturation_queries.require_auth")
    @patch("schema.facturation_queries.require_role")
    def test_factures_count(self, mock_role, mock_auth, mock_client) -> None:
        mock_auth.return_value = MagicMock(role="ADMIN")
        mock_client.count_factures.return_value = 7
        info = MagicMock()
        result = FacturationQueries().factures_count(info, campagne_id="camp-001")
        self.assertEqual(result, 7)
        mock_client.count_factures.assert_called_once_with(campagne_id="camp-001", abonne_id="", statut="")

    @patch("schema.facturation_queries.facturation_client")
    @patch("schema.facturation_queries.require_auth")
    @patch("schema.facturation_queries.require_role")
    def test_factures_par_campagne(self, mock_role, mock_auth, mock_client) -> None:
        mock_auth.return_value = MagicMock(role="ADMIN")
        mock_client.get_factures_par_campagne.return_value = MagicMock(factures=[_facture_response()])
        info = MagicMock()
        result = FacturationQueries().factures_par_campagne(info, campagne_id="camp-001")
        self.assertEqual(len(result), 1)

    @patch("schema.facturation_queries.campagne_client")
    @patch("schema.facturation_queries.abonne_client")
    @patch("schema.facturation_queries.facturation_client")
    @patch("schema.facturation_queries.require_auth")
    @patch("schema.facturation_queries.require_role")
    def test_factures_enrichies_nom_abonne_et_campagne(
        self, mock_role, mock_auth, mock_fact, mock_abonne, mock_campagne
    ) -> None:
        """Le COMPTABLE (sans accès Abonné/Campagne) obtient nom d'abonné +
        nom/période de campagne directement sur la facture (enrichissement gateway)."""
        mock_auth.return_value = MagicMock(role="COMPTABLE")
        mock_fact.list_factures.return_value = MagicMock(factures=[_facture_response()])
        mock_abonne.list_abonnes.return_value = MagicMock(
            abonnes=[MagicMock(abonne_id="abonne-001", nom="Mbarga", prenom="Paul", numero_abonne="AB-0001")]
        )
        mock_campagne.list_campagnes.return_value = MagicMock(
            campagnes=[MagicMock(campagne_id="camp-001", nom="Août 2026", periode_mois=8, periode_annee=2026)]
        )
        result = FacturationQueries().factures(MagicMock())
        self.assertEqual(result[0].abonne_nom, "Paul Mbarga")
        self.assertEqual(result[0].abonne_numero, "AB-0001")
        self.assertEqual(result[0].campagne_nom, "Août 2026")
        self.assertEqual(result[0].campagne_periode_mois, 8)
        self.assertEqual(result[0].campagne_periode_annee, 2026)

    @patch("schema.facturation_queries.campagne_client")
    @patch("schema.facturation_queries.abonne_client")
    @patch("schema.facturation_queries.facturation_client")
    @patch("schema.facturation_queries.require_auth")
    @patch("schema.facturation_queries.require_role")
    def test_factures_enrichissement_degrade_si_service_indispo(
        self, mock_role, mock_auth, mock_fact, mock_abonne, mock_campagne
    ) -> None:
        """Si Abonné/Campagne est indisponible, la facture est renvoyée sans
        libellés (best-effort) — jamais d'échec de la requête."""
        mock_auth.return_value = MagicMock(role="COMPTABLE")
        mock_fact.list_factures.return_value = MagicMock(factures=[_facture_response()])
        mock_abonne.list_abonnes.side_effect = RuntimeError("Abonné indisponible")
        mock_campagne.list_campagnes.side_effect = RuntimeError("Campagne indisponible")
        result = FacturationQueries().factures(MagicMock())
        self.assertEqual(len(result), 1)
        self.assertEqual(result[0].abonne_nom, "")
        self.assertEqual(result[0].campagne_nom, "")


class TestFacturationMutations(SimpleTestCase):
    @patch("schema.facturation_mutations.facturation_client")
    @patch("schema.facturation_mutations.require_auth")
    @patch("schema.facturation_mutations.require_role")
    def test_update_tarif_admin(self, mock_role, mock_auth, mock_client) -> None:
        mock_auth.return_value = MagicMock(role="ADMIN")
        mock_client.update_tarif.return_value = _tarif_response(prix_m3=600.0)
        info = MagicMock()
        result = FacturationMutations().update_tarif(info, prix_m3=600.0, date_effet="2026-08-01")
        self.assertEqual(result.prix_m3, 600.0)
        mock_client.update_tarif.assert_called_once_with(prix_m3=600.0, date_effet="2026-08-01")

    @patch("schema.facturation_mutations.facturation_client")
    @patch("schema.facturation_mutations.require_auth")
    @patch("schema.facturation_mutations.require_role")
    def test_generer_factures(self, mock_role, mock_auth, mock_client) -> None:
        mock_auth.return_value = MagicMock(role="COMPTABLE")
        mock_client.generer_factures.return_value = MagicMock(factures=[_facture_response()])
        info = MagicMock()
        result = FacturationMutations().generer_factures(info, campagne_id="camp-001")
        self.assertEqual(len(result), 1)

    @patch("schema.facturation_mutations.notification_client")
    @patch("schema.facturation_mutations.facturation_client")
    @patch("schema.facturation_mutations.require_auth")
    @patch("schema.facturation_mutations.require_role")
    def test_envoyer_toutes_factures_whatsapp_compte_les_succes(
        self, mock_role, mock_auth, mock_fact_client, mock_notif_client
    ) -> None:
        import grpc

        mock_auth.return_value = MagicMock(role="ADMIN")
        mock_fact_client.get_factures_par_campagne.return_value = MagicMock(
            factures=[_facture_response(facture_id="f1"), _facture_response(facture_id="f2")]
        )
        # f1 réussit, f2 échoue — dégradation gracieuse, ne doit pas interrompre le lot
        mock_notif_client.renvoyer_facture.side_effect = [None, grpc.RpcError("échec")]
        info = MagicMock()
        result = FacturationMutations().envoyer_toutes_factures_whatsapp(info, campagne_id="camp-001")
        self.assertEqual(result, 1)

    @patch("schema.facturation_mutations.facturation_client")
    @patch("schema.facturation_mutations.require_auth")
    @patch("schema.facturation_mutations.require_role")
    def test_update_statut_facture(self, mock_role, mock_auth, mock_client) -> None:
        mock_auth.return_value = MagicMock(role="COMPTABLE")
        mock_client.update_statut_facture.return_value = _facture_response(statut="PAYEE")
        info = MagicMock()
        result = FacturationMutations().update_statut_facture(info, facture_id="facture-001", statut="PAYEE")
        self.assertEqual(result.statut, "PAYEE")
