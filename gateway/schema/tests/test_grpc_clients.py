from unittest.mock import Mock

from django.test import SimpleTestCase

from schema.grpc_clients import AbonneServiceClient, AuthServiceClient, FacturationServiceClient, PaiementServiceClient


class AuthServiceClientTests(SimpleTestCase):
    def setUp(self):
        self.client = AuthServiceClient()
        self.client._stub = Mock()

    def test_login(self):
        self.client.login("user", "pass")
        request = self.client._stub.Login.call_args[0][0]
        self.assertEqual((request.identifier, request.password), ("user", "pass"))

    def test_validate_token(self):
        self.client.validate_token("tok")
        self.assertEqual(self.client._stub.ValidateToken.call_args[0][0].token, "tok")

    def test_refresh_token(self):
        self.client.refresh_token("refresh-tok")
        self.assertEqual(self.client._stub.RefreshToken.call_args[0][0].refresh_token, "refresh-tok")

    def test_logout(self):
        self.client.logout("tok")
        self.assertEqual(self.client._stub.Logout.call_args[0][0].token, "tok")

    def test_create_user(self):
        self.client.create_user("agent1", "+237690000001", "AGENT", "agent1@example.com")
        request = self.client._stub.CreateUser.call_args[0][0]
        self.assertEqual(
            (request.username, request.phone_number, request.email, request.role),
            ("agent1", "+237690000001", "agent1@example.com", "AGENT"),
        )

    def test_deactivate_user(self):
        self.client.deactivate_user("user-1")
        self.assertEqual(self.client._stub.DeactivateUser.call_args[0][0].user_id, "user-1")

    def test_get_user(self):
        self.client.get_user("user-1")
        self.assertEqual(self.client._stub.GetUser.call_args[0][0].user_id, "user-1")

    def test_request_password_reset(self):
        self.client.request_password_reset("a@example.com")
        self.assertEqual(self.client._stub.RequestPasswordReset.call_args[0][0].email, "a@example.com")

    def test_set_password_with_token(self):
        self.client.set_password_with_token("tok", "newpass")
        request = self.client._stub.SetPasswordWithToken.call_args[0][0]
        self.assertEqual((request.token, request.new_password), ("tok", "newpass"))


class AbonneServiceClientTests(SimpleTestCase):
    def setUp(self):
        self.client = AbonneServiceClient()
        self.client._stub = Mock()

    def test_get_abonne(self):
        self.client.get_abonne("abonne-1")
        self.assertEqual(self.client._stub.GetAbonne.call_args[0][0].abonne_id, "abonne-1")

    def test_list_abonnes(self):
        self.client.list_abonnes("ACTIF")
        self.assertEqual(self.client._stub.ListAbonnes.call_args[0][0].statut, "ACTIF")

    def test_list_abonnes_sans_pagination_ne_transmet_pas_limit_offset(self):
        # Rétrocompatibilité stricte : omis, les champs proto3 `optional`
        # restent non définis (`HasField` renvoie `False` côté serveur).
        self.client.list_abonnes("ACTIF")
        request = self.client._stub.ListAbonnes.call_args[0][0]
        self.assertFalse(request.HasField("limit"))
        self.assertFalse(request.HasField("offset"))

    def test_list_abonnes_avec_pagination(self):
        self.client.list_abonnes("ACTIF", limit=5, offset=10)
        request = self.client._stub.ListAbonnes.call_args[0][0]
        self.assertEqual((request.statut, request.limit, request.offset), ("ACTIF", 5, 10))

    def test_count_abonnes_demande_une_page_de_taille_zero(self):
        self.client._stub.ListAbonnes.return_value = Mock(total=42)
        total = self.client.count_abonnes("ACTIF")
        self.assertEqual(total, 42)
        request = self.client._stub.ListAbonnes.call_args[0][0]
        self.assertEqual((request.limit, request.offset), (0, 0))

    def test_list_abonnes_actifs(self):
        self.client.list_abonnes_actifs()
        self.client._stub.ListAbonnesActifs.assert_called_once()

    def test_create_abonne(self):
        self.client.create_abonne(
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
        request = self.client._stub.CreateAbonne.call_args[0][0]
        self.assertEqual(request.nom, "Doe")
        self.assertEqual(request.numero_compteur, 1)

    def test_update_abonne(self):
        self.client.update_abonne("abonne-1", nom="Smith", prenom="", telephone_whatsapp="", adresse="")
        request = self.client._stub.UpdateAbonne.call_args[0][0]
        self.assertEqual((request.abonne_id, request.nom), ("abonne-1", "Smith"))

    def test_suspendre_abonne(self):
        self.client.suspendre_abonne("abonne-1")
        self.assertEqual(self.client._stub.SuspendreAbonne.call_args[0][0].abonne_id, "abonne-1")

    def test_reactiver_abonne(self):
        self.client.reactiver_abonne("abonne-1")
        self.assertEqual(self.client._stub.ReactiverAbonne.call_args[0][0].abonne_id, "abonne-1")

    def test_remplacer_compteur(self):
        self.client.remplacer_compteur(
            "abonne-1",
            index_fermeture=100.0,
            nouveau_numero_compteur=2,
            nouveau_quartier="Q",
            nouveau_camp=2,
            nouvel_index_initial=0.0,
            date_remplacement="2024-06-01",
        )
        request = self.client._stub.RemplacerCompteur.call_args[0][0]
        self.assertEqual((request.abonne_id, request.nouveau_numero_compteur), ("abonne-1", 2))


class FacturationServiceClientTests(SimpleTestCase):
    def setUp(self):
        self.client = FacturationServiceClient()
        self.client._stub = Mock()

    def test_list_factures_sans_pagination_ne_transmet_pas_limit_offset(self):
        self.client.list_factures(campagne_id="camp-1")
        request = self.client._stub.ListFactures.call_args[0][0]
        self.assertEqual(request.campagne_id, "camp-1")
        self.assertFalse(request.HasField("limit"))
        self.assertFalse(request.HasField("offset"))

    def test_list_factures_avec_pagination(self):
        self.client.list_factures(campagne_id="camp-1", limit=10, offset=20)
        request = self.client._stub.ListFactures.call_args[0][0]
        self.assertEqual((request.limit, request.offset), (10, 20))

    def test_count_factures_demande_une_page_de_taille_zero(self):
        self.client._stub.ListFactures.return_value = Mock(total=7)
        total = self.client.count_factures(campagne_id="camp-1")
        self.assertEqual(total, 7)
        request = self.client._stub.ListFactures.call_args[0][0]
        self.assertEqual((request.limit, request.offset), (0, 0))


class PaiementServiceClientTests(SimpleTestCase):
    def setUp(self):
        self.client = PaiementServiceClient()
        self.client._stub = Mock()

    def test_list_paiements_sans_pagination_ne_transmet_pas_limit_offset(self):
        self.client.list_paiements(facture_id="facture-1")
        request = self.client._stub.ListPaiements.call_args[0][0]
        self.assertEqual(request.facture_id, "facture-1")
        self.assertFalse(request.HasField("limit"))
        self.assertFalse(request.HasField("offset"))

    def test_list_paiements_avec_pagination(self):
        self.client.list_paiements(facture_id="facture-1", limit=3, offset=6)
        request = self.client._stub.ListPaiements.call_args[0][0]
        self.assertEqual((request.limit, request.offset), (3, 6))

    def test_count_paiements_demande_une_page_de_taille_zero(self):
        self.client._stub.ListPaiements.return_value = Mock(total=15)
        total = self.client.count_paiements(facture_id="facture-1")
        self.assertEqual(total, 15)
        request = self.client._stub.ListPaiements.call_args[0][0]
        self.assertEqual((request.limit, request.offset), (0, 0))
