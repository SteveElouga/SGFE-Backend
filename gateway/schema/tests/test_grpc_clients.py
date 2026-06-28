from unittest.mock import Mock

from django.test import SimpleTestCase

from schema.grpc_clients import AbonneServiceClient, AuthServiceClient


class AuthServiceClientTests(SimpleTestCase):
    def setUp(self):
        self.client = AuthServiceClient()
        self.client._stub = Mock()

    def test_login(self):
        self.client.login("user", "pass")
        request = self.client._stub.Login.call_args[0][0]
        self.assertEqual((request.username, request.password), ("user", "pass"))

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
        self.client.create_user("agent1", "agent1@example.com", "AGENT")
        request = self.client._stub.CreateUser.call_args[0][0]
        self.assertEqual((request.username, request.email, request.role), ("agent1", "agent1@example.com", "AGENT"))

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
