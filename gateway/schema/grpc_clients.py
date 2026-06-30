import sys
from pathlib import Path

import grpc
from django.conf import settings

sys.path.insert(0, str(Path(settings.BASE_DIR) / "proto"))

import abonne_service_pb2 as abonne_pb
import abonne_service_pb2_grpc as abonne_pb_grpc
import auth_service_pb2 as auth_pb
import auth_service_pb2_grpc as auth_pb_grpc
import config_service_pb2 as config_pb
import config_service_pb2_grpc as config_pb_grpc


class AuthServiceClient:
    """Client gRPC vers auth-service:50051 (voir proto/auth_service.proto)."""

    def __init__(self) -> None:
        address = f"{settings.AUTH_GRPC_HOST}:{settings.AUTH_GRPC_PORT}"
        self._channel = grpc.insecure_channel(address)
        self._stub = auth_pb_grpc.AuthServiceStub(self._channel)

    def login(self, identifier: str, password: str) -> auth_pb.TokenResponse:
        return self._stub.Login(auth_pb.LoginRequest(identifier=identifier, password=password))

    def validate_token(self, token: str) -> auth_pb.UserPayload:
        return self._stub.ValidateToken(auth_pb.TokenRequest(token=token))

    def refresh_token(self, refresh_token: str) -> auth_pb.TokenResponse:
        return self._stub.RefreshToken(auth_pb.RefreshRequest(refresh_token=refresh_token))

    def logout(self, token: str) -> auth_pb.StatusResponse:
        return self._stub.Logout(auth_pb.TokenRequest(token=token))

    def create_user(self, username: str, phone_number: str, role: str, email: str = "") -> auth_pb.UserResponse:
        return self._stub.CreateUser(
            auth_pb.CreateUserRequest(username=username, email=email, phone_number=phone_number, role=role)
        )

    def update_user(
        self, user_id: str, email: str = "", role: str = "", phone_number: str = ""
    ) -> auth_pb.UserResponse:
        return self._stub.UpdateUser(
            auth_pb.UpdateUserRequest(user_id=user_id, email=email, role=role, phone_number=phone_number)
        )

    def deactivate_user(self, user_id: str) -> auth_pb.UserResponse:
        return self._stub.DeactivateUser(auth_pb.UserIdRequest(user_id=user_id))

    def get_user(self, user_id: str) -> auth_pb.UserResponse:
        return self._stub.GetUser(auth_pb.UserIdRequest(user_id=user_id))

    def list_users(self) -> auth_pb.ListUsersResponse:
        return self._stub.ListUsers(auth_pb.EmptyRequest())

    def request_password_reset(self, email: str) -> auth_pb.StatusResponse:
        return self._stub.RequestPasswordReset(auth_pb.EmailRequest(email=email))

    def set_password_with_token(self, token: str, new_password: str) -> auth_pb.StatusResponse:
        return self._stub.SetPasswordWithToken(auth_pb.SetPasswordRequest(token=token, new_password=new_password))

    def request_phone_otp(self, phone_number: str) -> auth_pb.StatusResponse:
        return self._stub.RequestPhoneOtp(auth_pb.PhoneRequest(phone_number=phone_number))

    def verify_otp_and_set_password(
        self, phone_number: str, otp_code: str, new_password: str
    ) -> auth_pb.StatusResponse:
        return self._stub.VerifyOtpAndSetPassword(
            auth_pb.VerifyOtpRequest(phone_number=phone_number, otp_code=otp_code, new_password=new_password)
        )


class AbonneServiceClient:
    """Client gRPC vers abonne-service:50052 (voir proto/abonne_service.proto)."""

    def __init__(self) -> None:
        address = f"{settings.ABONNE_GRPC_HOST}:{settings.ABONNE_GRPC_PORT}"
        self._channel = grpc.insecure_channel(address)
        self._stub = abonne_pb_grpc.AbonneServiceStub(self._channel)

    def get_abonne(self, abonne_id: str) -> abonne_pb.AbonneResponse:
        return self._stub.GetAbonne(abonne_pb.AbonneIdRequest(abonne_id=abonne_id))

    def list_abonnes(self, statut: str = "") -> abonne_pb.ListAbonnesResponse:
        return self._stub.ListAbonnes(abonne_pb.ListAbonnesRequest(statut=statut))

    def list_abonnes_actifs(self) -> abonne_pb.ListAbonnesResponse:
        return self._stub.ListAbonnesActifs(abonne_pb.EmptyRequest())

    def create_abonne(self, **kwargs) -> abonne_pb.AbonneResponse:
        return self._stub.CreateAbonne(abonne_pb.CreateAbonneRequest(**kwargs))

    def update_abonne(self, abonne_id: str, **kwargs) -> abonne_pb.AbonneResponse:
        return self._stub.UpdateAbonne(abonne_pb.UpdateAbonneRequest(abonne_id=abonne_id, **kwargs))

    def suspendre_abonne(self, abonne_id: str) -> abonne_pb.AbonneResponse:
        return self._stub.SuspendreAbonne(abonne_pb.AbonneIdRequest(abonne_id=abonne_id))

    def reactiver_abonne(self, abonne_id: str) -> abonne_pb.AbonneResponse:
        return self._stub.ReactiverAbonne(abonne_pb.AbonneIdRequest(abonne_id=abonne_id))

    def resilier_abonne(self, abonne_id: str) -> abonne_pb.AbonneResponse:
        return self._stub.ResilierAbonne(abonne_pb.AbonneIdRequest(abonne_id=abonne_id))

    def update_compteur(self, abonne_id: str, **kwargs) -> abonne_pb.CompteurResponse:
        return self._stub.UpdateCompteur(abonne_pb.UpdateCompteurRequest(abonne_id=abonne_id, **kwargs))

    def remplacer_compteur(self, abonne_id: str, **kwargs) -> abonne_pb.CompteurResponse:
        return self._stub.RemplacerCompteur(abonne_pb.RemplacerCompteurRequest(abonne_id=abonne_id, **kwargs))

    def get_historique_compteur(self, abonne_id: str) -> abonne_pb.ListHistoriqueResponse:
        return self._stub.GetHistoriqueCompteur(abonne_pb.AbonneIdRequest(abonne_id=abonne_id))


class ConfigServiceClient:
    """Client gRPC vers config-service:50058 (voir proto/config_service.proto)."""

    def __init__(self) -> None:
        address = f"{settings.CONFIG_GRPC_HOST}:{settings.CONFIG_GRPC_PORT}"
        self._channel = grpc.insecure_channel(address)
        self._stub = config_pb_grpc.ConfigServiceStub(self._channel)

    def get_infos_societe(self) -> config_pb.InfosSocieteResponse:
        return self._stub.GetInfosSociete(config_pb.EmptyRequest())

    def update_infos_societe(self, **kwargs) -> config_pb.InfosSocieteResponse:
        return self._stub.UpdateInfosSociete(config_pb.UpdateInfosRequest(**kwargs))

    def get_config(self, cle: str) -> config_pb.ConfigResponse:
        return self._stub.GetConfig(config_pb.ConfigKeyRequest(cle=cle))

    def update_config(self, cle: str, valeur: str) -> config_pb.ConfigResponse:
        return self._stub.UpdateConfig(config_pb.UpdateConfigRequest(cle=cle, valeur=valeur))

    def list_configs(self) -> config_pb.ListConfigsResponse:
        return self._stub.ListConfigs(config_pb.EmptyRequest())


auth_client = AuthServiceClient()
abonne_client = AbonneServiceClient()
config_client = ConfigServiceClient()
