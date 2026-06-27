import sys
from concurrent import futures
from pathlib import Path

import grpc
from django.conf import settings

sys.path.insert(0, str(Path(settings.BASE_DIR) / "proto"))

import auth_service_pb2 as pb
import auth_service_pb2_grpc as pb_grpc

from comptes.serializers import user_to_payload, user_to_response
from comptes.services import AuthenticationError, AuthService, UserAdminService


class AuthServiceServicer(pb_grpc.AuthServiceServicer):
    def __init__(self) -> None:
        self.auth_service = AuthService()
        self.user_admin_service = UserAdminService()

    def Login(self, request, context):
        try:
            access, refresh, expires_in = self.auth_service.login(request.username, request.password)
        except AuthenticationError as exc:
            context.abort(grpc.StatusCode.UNAUTHENTICATED, str(exc))
        return pb.TokenResponse(access_token=access, refresh_token=refresh, expires_in=expires_in)

    def ValidateToken(self, request, context):
        try:
            user = self.auth_service.validate_token(request.token)
        except AuthenticationError as exc:
            context.abort(grpc.StatusCode.UNAUTHENTICATED, str(exc))
        return pb.UserPayload(**user_to_payload(user))

    def RefreshToken(self, request, context):
        try:
            access, refresh, expires_in = self.auth_service.refresh_token(request.refresh_token)
        except AuthenticationError as exc:
            context.abort(grpc.StatusCode.UNAUTHENTICATED, str(exc))
        return pb.TokenResponse(access_token=access, refresh_token=refresh, expires_in=expires_in)

    def Logout(self, request, context):
        try:
            self.auth_service.logout(request.token)
        except AuthenticationError as exc:
            return pb.StatusResponse(success=False, message=str(exc))
        return pb.StatusResponse(success=True, message="Déconnexion réussie")

    def CreateUser(self, request, context):
        user = self.user_admin_service.create_user(
            username=request.username, email=request.email, password=request.password, role=request.role
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


def serve() -> None:
    server = grpc.server(futures.ThreadPoolExecutor(max_workers=10))
    pb_grpc.add_AuthServiceServicer_to_server(AuthServiceServicer(), server)
    server.add_insecure_port(f"[::]:{settings.AUTH_GRPC_PORT}")
    server.start()
    print(f"Auth gRPC server démarré sur le port {settings.AUTH_GRPC_PORT}")
    server.wait_for_termination()
