import sys
from concurrent import futures
from pathlib import Path

import grpc
from django.conf import settings

sys.path.insert(0, str(Path(settings.BASE_DIR) / "proto"))

import auth_service_pb2 as pb
import auth_service_pb2_grpc as pb_grpc

from comptes.event_publisher import publish_user_event
from comptes.grpc_interceptors import ErrorHandlingInterceptor
from comptes.grpc_auth import AuthServerInterceptor, ouvrir_port_grpc
from comptes.serializers import user_to_payload, user_to_response
from comptes.services import AuthService, PasswordSetupService, PhoneOtpService, UserAdminService


class AuthServiceServicer(pb_grpc.AuthServiceServicer):
    """Les exceptions (AuthenticationError, ObjectDoesNotExist, IntegrityError)
    ne sont pas interceptées ici : ErrorHandlingInterceptor s'en charge de
    façon centralisée pour toutes les méthodes (voir grpc_interceptors.py).
    """

    def __init__(self) -> None:
        self.auth_service = AuthService()
        self.user_admin_service = UserAdminService()
        self.password_setup_service = PasswordSetupService()
        self.phone_otp_service = PhoneOtpService()

    def Login(self, request, context):
        access, refresh, expires_in = self.auth_service.login(request.identifier, request.password)
        return pb.TokenResponse(access_token=access, refresh_token=refresh, expires_in=expires_in)

    def ValidateToken(self, request, context):
        user = self.auth_service.validate_token(request.token)
        return pb.UserPayload(**user_to_payload(user))

    def RefreshToken(self, request, context):
        access, refresh, expires_in = self.auth_service.refresh_token(request.refresh_token)
        return pb.TokenResponse(access_token=access, refresh_token=refresh, expires_in=expires_in)

    def Logout(self, request, context):
        # Un token invalide/expiré lève AuthenticationError, gérée par
        # ErrorHandlingInterceptor (-> UNAUTHENTICATED) comme les 12 autres
        # RPC de ce servicer (voir ANO-020) — pas de catch local ici.
        self.auth_service.logout(request.token)
        return pb.StatusResponse(success=True, message="Déconnexion réussie")

    def CreateUser(self, request, context):
        user = self.user_admin_service.create_user(
            username=request.username,
            email=request.email,
            phone_number=request.phone_number,
            role=request.role,
        )
        publish_user_event(str(user.id), "USER_CREATED")
        return pb.UserResponse(**user_to_response(user))

    def UpdateUser(self, request, context):
        user = self.user_admin_service.update_user(
            user_id=request.user_id,
            email=request.email,
            role=request.role,
            phone_number=request.phone_number,
        )
        # Couvre notamment le changement de rôle (cas sécurité côté front).
        publish_user_event(str(user.id), "USER_UPDATED")
        return pb.UserResponse(**user_to_response(user))

    def DeactivateUser(self, request, context):
        user = self.user_admin_service.deactivate_user(request.user_id, caller_id=request.caller_id)
        publish_user_event(str(user.id), "USER_UPDATED")
        return pb.UserResponse(**user_to_response(user))

    def ReactivateUser(self, request, context):
        user = self.user_admin_service.reactivate_user(request.user_id)
        publish_user_event(str(user.id), "USER_UPDATED")
        return pb.UserResponse(**user_to_response(user))

    def ResetUserPassword(self, request, context):
        user = self.user_admin_service.resend_credentials(request.user_id)
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

    def RequestPhoneOtp(self, request, context):
        self.phone_otp_service.request_otp_by_phone(request.phone_number)
        return pb.StatusResponse(success=True, message="Si ce numéro est enregistré, un code a été envoyé par WhatsApp")

    def VerifyOtpAndSetPassword(self, request, context):
        self.phone_otp_service.verify_otp_and_set_password(
            phone_number=request.phone_number,
            otp_code=request.otp_code,
            new_password=request.new_password,
        )
        return pb.StatusResponse(success=True, message="Mot de passe défini")


def serve() -> None:
    server = grpc.server(
        futures.ThreadPoolExecutor(max_workers=10),
        interceptors=[AuthServerInterceptor(settings.INTERNAL_GRPC_KEY), ErrorHandlingInterceptor()],
    )
    pb_grpc.add_AuthServiceServicer_to_server(AuthServiceServicer(), server)
    ouvrir_port_grpc(server, settings.AUTH_GRPC_PORT)
    server.start()
    print(f"Auth gRPC server démarré sur le port {settings.AUTH_GRPC_PORT}")
    server.wait_for_termination()
