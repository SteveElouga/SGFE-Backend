import sys
from pathlib import Path

import grpc
from django.conf import settings

sys.path.insert(0, str(Path(settings.BASE_DIR) / "proto"))

import auth_service_pb2 as pb
import auth_service_pb2_grpc as pb_grpc


class AuthServiceClient:
    """Client gRPC vers auth-service:50051 (voir proto/auth_service.proto)."""

    def __init__(self) -> None:
        address = f"{settings.AUTH_GRPC_HOST}:{settings.AUTH_GRPC_PORT}"
        self._channel = grpc.insecure_channel(address)
        self._stub = pb_grpc.AuthServiceStub(self._channel)

    def login(self, username: str, password: str) -> pb.TokenResponse:
        return self._stub.Login(pb.LoginRequest(username=username, password=password))

    def validate_token(self, token: str) -> pb.UserPayload:
        return self._stub.ValidateToken(pb.TokenRequest(token=token))

    def refresh_token(self, refresh_token: str) -> pb.TokenResponse:
        return self._stub.RefreshToken(pb.RefreshRequest(refresh_token=refresh_token))

    def logout(self, token: str) -> pb.StatusResponse:
        return self._stub.Logout(pb.TokenRequest(token=token))

    def create_user(self, username: str, email: str, role: str) -> pb.UserResponse:
        return self._stub.CreateUser(pb.CreateUserRequest(username=username, email=email, role=role))

    def deactivate_user(self, user_id: str) -> pb.UserResponse:
        return self._stub.DeactivateUser(pb.UserIdRequest(user_id=user_id))

    def get_user(self, user_id: str) -> pb.UserResponse:
        return self._stub.GetUser(pb.UserIdRequest(user_id=user_id))

    def request_password_reset(self, email: str) -> pb.StatusResponse:
        return self._stub.RequestPasswordReset(pb.EmailRequest(email=email))

    def set_password_with_token(self, token: str, new_password: str) -> pb.StatusResponse:
        return self._stub.SetPasswordWithToken(pb.SetPasswordRequest(token=token, new_password=new_password))


auth_client = AuthServiceClient()
