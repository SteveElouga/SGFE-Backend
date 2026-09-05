from unittest.mock import Mock

from django.test import SimpleTestCase

from schema.grpc_clients import AbonneServiceClient, AuthServiceClient, FacturationServiceClient, PaiementServiceClient


class AuthServiceClientTests(SimpleTestCase):
    def setUp(self) -> None:
        self.grpc_client = AuthServiceClient()
        self.grpc_client._stub = Mock()

    def test_login(self) -> None:
        self.grpc_client.login("user", "pass")
        request = self.grpc_client._stub.Login.call_args[0][0]
        self.assertEqual((request.identifier, request.password), ("user", "pass"))

    def test_validate_token(self) -> None:
        self.grpc_client.validate_token("tok")
        self.assertEqual(self.grpc_client._stub.ValidateToken.call_args[0][0].token, "tok")

    def test_refresh_token(self) -> None:
        self.grpc_client.refresh_token("refresh-tok")
        self.assertEqual(self.grpc_client._stub.RefreshToken.call_args[0][0].refresh_token, "refresh-tok")

    def test_logout(self) -> None:
        self.grpc_client.logout("tok")
        self.assertEqual(self.grpc_client._stub.Logout.call_args[0][0].token, "tok")

    def test_create_user(self) -> None:
        self.grpc_client.create_user("agent1", "+237690000001", "AGENT", "agent1@example.com")
        request = self.grpc_client._stub.CreateUser.call_args[0][0]
        self.assertEqual(
            (request.username, request.phone_number, request.email, request.role),
            ("agent1", "+237690000001", "agent1@example.com", "AGENT"),
        )

    def test_deactivate_user(self) -> None:
        self.grpc_client.deactivate_user("user-1")
        self.assertEqual(self.grpc_client._stub.DeactivateUser.call_args[0][0].user_id, "user-1")

    def test_get_user(self) -> None:
        self.grpc_client.get_user("user-1")
        self.assertEqual(self.grpc_client._stub.GetUser.call_args[0][0].user_id, "user-1")

    def test_request_password_reset(self) -> None:
        self.grpc_client.request_password_reset("a@example.com")
        self.assertEqual(self.grpc_client._stub.RequestPasswordReset.call_args[0][0].email, "a@example.com")

    def test_set_password_with_token(self) -> None:
        self.grpc_client.set_password_with_token("tok", "newpass")
        request = self.grpc_client._stub.SetPasswordWithToken.call_args[0][0]
        self.assertEqual((request.token, request.new_password), ("tok", "newpass"))

    def test_enregistrer_evenement_securite(self) -> None:
        self.grpc_client.enregistrer_evenement_securite(
            type_evenement="ROLE_REFUSE",
            detail="rôle insuffisant",
            acteur_id="u-1",
            acteur_nom="bob",
            acteur_role="AGENT",
            request_id="req-1",
        )
        request = self.grpc_client._stub.EnregistrerEvenementSecurite.call_args[0][0]
        self.assertEqual(
            (request.type_evenement, request.detail, request.acteur_id, request.acteur_nom, request.request_id),
            ("ROLE_REFUSE", "rôle insuffisant", "u-1", "bob", "req-1"),
        )


class AbonneServiceClientTests(SimpleTestCase):
    def setUp(self) -> None:
        self.grpc_client = AbonneServiceClient()
        self.grpc_client._stub = Mock()

    def test_get_abonne(self) -> None:
        self.grpc_client.get_abonne("abonne-1")
        self.assertEqual(self.grpc_client._stub.GetAbonne.call_args[0][0].abonne_id, "abonne-1")

    def test_list_abonnes(self) -> None:
        self.grpc_client.list_abonnes("ACTIF")
        self.assertEqual(self.grpc_client._stub.ListAbonnes.call_args[0][0].statut, "ACTIF")

    def test_list_abonnes_sans_pagination_ne_transmet_pas_limit_offset(self) -> None:
        # Rétrocompatibilité stricte : omis, les champs proto3 `optional`
        # restent non définis (`HasField` renvoie `False` côté serveur).
        self.grpc_client.list_abonnes("ACTIF")
        request = self.grpc_client._stub.ListAbonnes.call_args[0][0]
        self.assertFalse(request.HasField("limit"))
        self.assertFalse(request.HasField("offset"))

    def test_list_abonnes_avec_pagination(self) -> None:
        self.grpc_client.list_abonnes("ACTIF", limit=5, offset=10)
        request = self.grpc_client._stub.ListAbonnes.call_args[0][0]
        self.assertEqual((request.statut, request.limit, request.offset), ("ACTIF", 5, 10))

    def test_count_abonnes_demande_une_page_de_taille_zero(self) -> None:
        self.grpc_client._stub.ListAbonnes.return_value = Mock(total=42)
        total = self.grpc_client.count_abonnes("ACTIF")
        self.assertEqual(total, 42)
        request = self.grpc_client._stub.ListAbonnes.call_args[0][0]
        self.assertEqual((request.limit, request.offset), (0, 0))

    def test_list_abonnes_actifs(self) -> None:
        self.grpc_client.list_abonnes_actifs()
        self.grpc_client._stub.ListAbonnesActifs.assert_called_once()

    def test_create_abonne(self) -> None:
        self.grpc_client.create_abonne(
            nom="Doe",
            prenom="John",
            telephone_whatsapp="+241",
            adresse="",
            numero_compteur=1,
            quartier="Centre",
            camp=1,
            index_initial=0.0,
            date_pose="2024-01-01",
        )
        request = self.grpc_client._stub.CreateAbonne.call_args[0][0]
        self.assertEqual(request.nom, "Doe")
        self.assertEqual(request.numero_compteur, 1)

    def test_update_abonne(self) -> None:
        self.grpc_client.update_abonne("abonne-1", nom="Smith", prenom="", telephone_whatsapp="", adresse="")
        request = self.grpc_client._stub.UpdateAbonne.call_args[0][0]
        self.assertEqual((request.abonne_id, request.nom), ("abonne-1", "Smith"))

    def test_suspendre_abonne(self) -> None:
        self.grpc_client.suspendre_abonne("abonne-1")
        self.assertEqual(self.grpc_client._stub.SuspendreAbonne.call_args[0][0].abonne_id, "abonne-1")

    def test_reactiver_abonne(self) -> None:
        self.grpc_client.reactiver_abonne("abonne-1")
        self.assertEqual(self.grpc_client._stub.ReactiverAbonne.call_args[0][0].abonne_id, "abonne-1")

    def test_remplacer_compteur(self) -> None:
        self.grpc_client.remplacer_compteur(
            "abonne-1",
            index_fermeture=100.0,
            nouveau_numero_compteur=2,
            nouveau_quartier="Q",
            nouveau_camp=2,
            nouvel_index_initial=0.0,
            date_remplacement="2024-06-01",
        )
        request = self.grpc_client._stub.RemplacerCompteur.call_args[0][0]
        self.assertEqual((request.abonne_id, request.nouveau_numero_compteur), ("abonne-1", 2))


class FacturationServiceClientTests(SimpleTestCase):
    def setUp(self) -> None:
        self.grpc_client = FacturationServiceClient()
        self.grpc_client._stub = Mock()

    def test_list_factures_sans_pagination_ne_transmet_pas_limit_offset(self) -> None:
        self.grpc_client.list_factures(campagne_id="camp-1")
        request = self.grpc_client._stub.ListFactures.call_args[0][0]
        self.assertEqual(request.campagne_id, "camp-1")
        self.assertFalse(request.HasField("limit"))
        self.assertFalse(request.HasField("offset"))

    def test_list_factures_avec_pagination(self) -> None:
        self.grpc_client.list_factures(campagne_id="camp-1", limit=10, offset=20)
        request = self.grpc_client._stub.ListFactures.call_args[0][0]
        self.assertEqual((request.limit, request.offset), (10, 20))

    def test_count_factures_demande_une_page_de_taille_zero(self) -> None:
        self.grpc_client._stub.ListFactures.return_value = Mock(total=7)
        total = self.grpc_client.count_factures(campagne_id="camp-1")
        self.assertEqual(total, 7)
        request = self.grpc_client._stub.ListFactures.call_args[0][0]
        self.assertEqual((request.limit, request.offset), (0, 0))


class PaiementServiceClientTests(SimpleTestCase):
    def setUp(self) -> None:
        self.grpc_client = PaiementServiceClient()
        self.grpc_client._stub = Mock()

    def test_list_paiements_sans_pagination_ne_transmet_pas_limit_offset(self) -> None:
        self.grpc_client.list_paiements(facture_id="facture-1")
        request = self.grpc_client._stub.ListPaiements.call_args[0][0]
        self.assertEqual(request.facture_id, "facture-1")
        self.assertFalse(request.HasField("limit"))
        self.assertFalse(request.HasField("offset"))

    def test_list_paiements_avec_pagination(self) -> None:
        self.grpc_client.list_paiements(facture_id="facture-1", limit=3, offset=6)
        request = self.grpc_client._stub.ListPaiements.call_args[0][0]
        self.assertEqual((request.limit, request.offset), (3, 6))

    def test_count_paiements_demande_une_page_de_taille_zero(self) -> None:
        self.grpc_client._stub.ListPaiements.return_value = Mock(total=15)
        total = self.grpc_client.count_paiements(facture_id="facture-1")
        self.assertEqual(total, 15)
        request = self.grpc_client._stub.ListPaiements.call_args[0][0]
        self.assertEqual((request.limit, request.offset), (0, 0))

    def test_creer_session_paiement(self) -> None:
        """Paiement en ligne (mock) — voir `passerelle_paiement.py`."""
        self.grpc_client.creer_session_paiement(facture_id="facture-1", montant=5000.0, token_espace="token-1")
        request = self.grpc_client._stub.CreerSessionPaiementEnLigne.call_args[0][0]
        self.assertEqual(
            (request.facture_id, request.montant, request.token_espace),
            ("facture-1", 5000.0, "token-1"),
        )

    def test_confirmer_session_paiement(self) -> None:
        self.grpc_client.confirmer_session_paiement(session_id="session-1", token_espace="token-1")
        request = self.grpc_client._stub.ConfirmerSessionPaiementEnLigne.call_args[0][0]
        self.assertEqual((request.session_id, request.token_espace), ("session-1", "token-1"))
