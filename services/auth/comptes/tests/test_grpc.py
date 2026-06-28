from django.test import TestCase

from comptes.grpc_server import AuthServiceServicer
from comptes.models import Role, User
from comptes.services import AuthenticationError
from proto import auth_service_pb2 as pb


class FakeContext:
    """Double minimal de grpc.ServicerContext.

    En appel direct (hors serveur gRPC réel), ErrorHandlingInterceptor n'est
    pas dans la boucle : les méthodes du servicer laissent donc simplement
    remonter l'exception Python (voir test_grpc_interceptors.py pour la
    conversion en code gRPC, elle, testée séparément).
    """

    def abort(self, code, details):
        raise AssertionError("context.abort() ne devrait pas être appelé directement par le servicer")


class AuthServiceServicerTests(TestCase):
    def setUp(self):
        self.servicer = AuthServiceServicer()
        self.context = FakeContext()
        self.user = User.objects.create_user(
            username="comptable_grpc", email="comptable_grpc@example.com", password="secret123", role=Role.COMPTABLE
        )

    def test_login_success(self):
        response = self.servicer.Login(
            pb.LoginRequest(username="comptable_grpc", password="secret123"), self.context
        )
        self.assertTrue(response.access_token)
        self.assertTrue(response.refresh_token)

    def test_login_failure_raises_authentication_error(self):
        with self.assertRaises(AuthenticationError):
            self.servicer.Login(pb.LoginRequest(username="comptable_grpc", password="wrong"), self.context)

    def test_validate_token_success(self):
        login = self.servicer.Login(
            pb.LoginRequest(username="comptable_grpc", password="secret123"), self.context
        )
        payload = self.servicer.ValidateToken(pb.TokenRequest(token=login.access_token), self.context)
        self.assertEqual(payload.username, "comptable_grpc")

    def test_validate_token_failure_raises_authentication_error(self):
        with self.assertRaises(AuthenticationError):
            self.servicer.ValidateToken(pb.TokenRequest(token="invalide"), self.context)

    def test_refresh_token_success(self):
        login = self.servicer.Login(
            pb.LoginRequest(username="comptable_grpc", password="secret123"), self.context
        )
        refreshed = self.servicer.RefreshToken(
            pb.RefreshRequest(refresh_token=login.refresh_token), self.context
        )
        self.assertTrue(refreshed.access_token)

    def test_refresh_token_failure_raises_authentication_error(self):
        with self.assertRaises(AuthenticationError):
            self.servicer.RefreshToken(pb.RefreshRequest(refresh_token="invalide"), self.context)

    def test_logout_success(self):
        login = self.servicer.Login(
            pb.LoginRequest(username="comptable_grpc", password="secret123"), self.context
        )
        response = self.servicer.Logout(pb.TokenRequest(token=login.access_token), self.context)
        self.assertTrue(response.success)

    def test_logout_failure_returns_unsuccessful_status(self):
        response = self.servicer.Logout(pb.TokenRequest(token="invalide"), self.context)
        self.assertFalse(response.success)

    def test_create_user(self):
        response = self.servicer.CreateUser(
            pb.CreateUserRequest(username="agent_grpc", email="agent_grpc@example.com", password="secret123", role=Role.AGENT),
            self.context,
        )
        self.assertEqual(response.username, "agent_grpc")
        self.assertTrue(response.user_id)

    def test_get_user(self):
        response = self.servicer.GetUser(pb.UserIdRequest(user_id=str(self.user.id)), self.context)
        self.assertEqual(response.username, "comptable_grpc")

    def test_update_user(self):
        response = self.servicer.UpdateUser(
            pb.UpdateUserRequest(user_id=str(self.user.id), email="updated@example.com", role=Role.ADMIN),
            self.context,
        )
        self.assertEqual(response.email, "updated@example.com")
        self.assertEqual(response.role, Role.ADMIN)

    def test_deactivate_user(self):
        response = self.servicer.DeactivateUser(pb.UserIdRequest(user_id=str(self.user.id)), self.context)
        self.assertFalse(response.is_active)

    def test_list_users(self):
        response = self.servicer.ListUsers(pb.EmptyRequest(), self.context)
        usernames = {u.username for u in response.users}
        self.assertIn("comptable_grpc", usernames)
