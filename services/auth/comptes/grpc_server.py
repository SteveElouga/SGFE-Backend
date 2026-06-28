import sys
from concurrent import futures
from pathlib import Path

import grpc
from django.conf import settings

sys.path.insert(0, str(Path(settings.BASE_DIR) / "proto"))

import auth_service_pb2 as pb
import auth_service_pb2_grpc as pb_grpc

from comptes.grpc_interceptors import ErrorHandlingInterceptor
from comptes.serializers import user_to_payload, user_to_response
from comptes.services import AuthenticationError, AuthService, PasswordSetupService, UserAdminService


class AuthServiceServicer(pb_grpc.AuthServiceServicer):
    """Les exceptions (AuthenticationError, ObjectDoesNotExist, IntegrityError)
    ne sont pas interceptées ici : ErrorHandlingInterceptor s'en charge de
    façon centralisée pour toutes les méthodes (voir grpc_interceptors.py).
    """

    def __init__(self) -> None:
        self.auth_service = AuthService()
        self.user_admin_service = UserAdminService()
        self.password_setup_service = PasswordSetupService()

    def Login(self, request, context):
        access, refresh, expires_in = self.auth_service.login(request.username, request.password)
        return pb.TokenResponse(access_token=access, refresh_token=refresh, expires_in=expires_in)

    def ValidateToken(self, request, context):
        user = self.auth_service.validate_token(request.token)
        return pb.UserPayload(**user_to_payload(user))

    def RefreshToken(self, request, context):
        access, refresh, expires_in = self.auth_service.refresh_token(request.refresh_token)
        return pb.TokenResponse(access_token=access, refresh_token=refresh, expires_in=expires_in)

    def Logout(self, request, context):
        try:
            self.auth_service.logout(request.token)
        except AuthenticationError as exc:
            return pb.StatusResponse(success=False, message=str(exc))
        return pb.StatusResponse(success=True, message="Déconnexion réussie")

    def CreateUser(self, request, context):
        user = self.user_admin_service.create_user(
            username=request.username, email=request.email, role=request.role
        )
        return pb.UserResponse(**user_to_response(user))

    def UpdateUser(self, request, context):
        user = self.user_admin_service.update_user(
            user_id=request.user_id, email=request.email, role=request.role
        )
        return pb.UserResponse(**user_to_response(user))

    def DeactivateUser(self, request, context):
        user = self.user_admin_service.deactivate_user(request.user_id)
        return pb.UserResponse(**user_to_response(user))

    def ListUsers(self, request, context):
        users = self.user_admin_service.list_users()
        return pb.ListUsersResponse(users=[pb.UserResponse(**user_to_response(u)) for u in users])

    def GetUser(self, request, context):
        user = self.user_admin_service.get_user(request.user_id)
        return pb.UserResponse(**user_to_response(user))

    def RequestPasswordReset(self, request, context):
        self.password_setup_service.request_password_reset(request.email)
        return pb.StatusResponse(success=True, message="Si ce compte existe, un e-mail a été envoyé")

    def SetPasswordWithToken(self, request, context):
        self.password_setup_service.set_password_with_token(request.token, request.new_password)
        return pb.StatusResponse(success=True, message="Mot de passe défini")


def serve() -> None:
    server = grpc.server(
        futures.ThreadPoolExecutor(max_workers=10), interceptors=[ErrorHandlingInterceptor()]
    )
    pb_grpc.add_AuthServiceServicer_to_server(AuthServiceServicer(), server)
    server.add_insecure_port(f"[::]:{settings.AUTH_GRPC_PORT}")
    server.start()
    print(f"Auth gRPC server démarré sur le port {settings.AUTH_GRPC_PORT}")
    server.wait_for_termination()
