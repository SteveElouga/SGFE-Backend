import json
import logging
import sys
from concurrent import futures
from pathlib import Path

import grpc
from django.conf import settings

sys.path.insert(0, str(Path(settings.BASE_DIR) / "proto"))

import auth_service_pb2 as pb
import auth_service_pb2_grpc as pb_grpc

from comptes.event_publisher import publish_user_event
from comptes.export import ExportService
from comptes.grpc_interceptors import ErrorHandlingInterceptor, IdentityInterceptor, get_caller
from comptes.grpc_auth import AuthServerInterceptor, ouvrir_port_grpc
from comptes.serializers import user_to_payload, user_to_response
from comptes.services import AuthService, PasswordSetupService, PhoneOtpService, UserAdminService

logger = logging.getLogger(__name__)

# Défense en profondeur (OWASP A01/API5, ASVS V8, SOC2 CC6) — voir
# docs/CONFORMITE_SOC2_OWASP.md §3.1/§3.3/§3.4 et le plan de remédiation
# item #3. La gateway reste l'unique point de DÉCISION RBAC
# (`services/auth/comptes/services.py:193`) : ce module ne fait que
# journaliser un avertissement quand l'identité propagée par
# `IdentityInterceptor` (PR #193, `get_caller()`) porte un rôle qui n'aurait
# pas dû franchir la gateway pour la désactivation d'un compte — sans jamais
# bloquer l'appel.
#
# Ensemble aligné sur le tableau "Rôles et permissions" du CLAUDE.md racine
# ("Gérer les utilisateurs" -> ADMIN uniquement) et sur
# `gateway/schema/auth_mutations.py` (`require_role(info, "ADMIN")`), qui
# applique déjà cette règle côté gateway.
_ROLES_AUTORISES_DEACTIVATE: frozenset[str] = frozenset({"ADMIN"})


def _revalider_role_deactivate(action: str) -> None:
    """Filet de sécurité : journalise un avertissement si l'identité propagée
    par la gateway ne correspond pas au rôle attendu pour `action`.

    Ne BLOQUE jamais l'appel — c'est un compromis assumé, pas un oubli.
    L'identité n'est pas propagée par tous les chemins d'appel légitimes
    aujourd'hui (ex. certains appels service-à-service internes n'ont pas
    d'identité utilisateur humaine) ; bloquer romprait ces appels sans gain
    réel puisque la gateway a déjà tranché en amont. Voir
    `docs/CONFORMITE_SOC2_OWASP.md` §3.1 A01 pour le constat d'origine.
    """
    caller = get_caller()
    if caller.is_anonyme:
        logger.warning(
            "Défense en profondeur — %s appelé sans identité propagée : revalidation de "
            "rôle impossible (appel direct hors gateway, ou appel service-à-service "
            "légitime sans identité utilisateur).",
            action,
        )
        return
    if caller.role not in _ROLES_AUTORISES_DEACTIVATE:
        logger.warning(
            "Défense en profondeur — %s appelé par %s (role=%s), hors de l'ensemble "
            "autorisé %s : la gateway aurait dû bloquer cet appel.",
            action,
            caller.username or caller.user_id,
            caller.role,
            sorted(_ROLES_AUTORISES_DEACTIVATE),
        )


class AuthServiceServicer(pb_grpc.AuthServiceServicer):  # type: ignore[misc]
    # ^ AuthServiceServicer vient du stub généré auth_service_pb2_grpc, exclu
    # de la vérification mypy (voir mypy.ini) — mypy le voit donc comme `Any`,
    # ce qui rend toute sous-classe de lui structurellement "misc" ; rien à
    # corriger côté code métier ici.
    """Les exceptions (AuthenticationError, ObjectDoesNotExist, IntegrityError)
    ne sont pas interceptées ici : ErrorHandlingInterceptor s'en charge de
    façon centralisée pour toutes les méthodes (voir grpc_interceptors.py).
    """

    def __init__(self) -> None:
        self.auth_service = AuthService()
        self.user_admin_service = UserAdminService()
        self.password_setup_service = PasswordSetupService()
        self.phone_otp_service = PhoneOtpService()
        # Construction sans I/O (aucun client gRPC externe, voir CLAUDE.md du
        # service) — sûre même si elle n'est jamais appelée.
        self.export_service = ExportService()

    def Login(self, request: pb.LoginRequest, context: grpc.ServicerContext) -> pb.TokenResponse:
        access, refresh, expires_in = self.auth_service.login(request.identifier, request.password)
        return pb.TokenResponse(access_token=access, refresh_token=refresh, expires_in=expires_in)

    def ValidateToken(self, request: pb.TokenRequest, context: grpc.ServicerContext) -> pb.UserPayload:
        user = self.auth_service.validate_token(request.token)
        return pb.UserPayload(**user_to_payload(user))

    def RefreshToken(self, request: pb.RefreshRequest, context: grpc.ServicerContext) -> pb.TokenResponse:
        access, refresh, expires_in = self.auth_service.refresh_token(request.refresh_token)
        return pb.TokenResponse(access_token=access, refresh_token=refresh, expires_in=expires_in)

    def Logout(self, request: pb.TokenRequest, context: grpc.ServicerContext) -> pb.StatusResponse:
        # Un token invalide/expiré lève AuthenticationError, gérée par
        # ErrorHandlingInterceptor (-> UNAUTHENTICATED) comme les 12 autres
        # RPC de ce servicer (voir ANO-020) — pas de catch local ici.
        self.auth_service.logout(request.token)
        return pb.StatusResponse(success=True, message="Déconnexion réussie")

    def CreateUser(self, request: pb.CreateUserRequest, context: grpc.ServicerContext) -> pb.UserResponse:
        user = self.user_admin_service.create_user(
            username=request.username,
            email=request.email,
            phone_number=request.phone_number,
            role=request.role,
        )
        publish_user_event(str(user.id), "USER_CREATED")
        return pb.UserResponse(**user_to_response(user))

    def UpdateUser(self, request: pb.UpdateUserRequest, context: grpc.ServicerContext) -> pb.UserResponse:
        user = self.user_admin_service.update_user(
            user_id=request.user_id,
            email=request.email,
            role=request.role,
            phone_number=request.phone_number,
        )
        # Couvre notamment le changement de rôle (cas sécurité côté front).
        publish_user_event(str(user.id), "USER_UPDATED")
        return pb.UserResponse(**user_to_response(user))

    def DeactivateUser(self, request: pb.DeactivateUserRequest, context: grpc.ServicerContext) -> pb.UserResponse:
        _revalider_role_deactivate("DeactivateUser")
        user = self.user_admin_service.deactivate_user(request.user_id, caller_id=request.caller_id)
        publish_user_event(str(user.id), "USER_UPDATED")
        return pb.UserResponse(**user_to_response(user))

    def ReactivateUser(self, request: pb.UserIdRequest, context: grpc.ServicerContext) -> pb.UserResponse:
        user = self.user_admin_service.reactivate_user(request.user_id)
        publish_user_event(str(user.id), "USER_UPDATED")
        return pb.UserResponse(**user_to_response(user))

    def AnonymiserUtilisateur(self, request: pb.UserIdRequest, context: grpc.ServicerContext) -> pb.UserResponse:
        user = self.user_admin_service.anonymiser_utilisateur(request.user_id)
        publish_user_event(str(user.id), "USER_UPDATED")
        return pb.UserResponse(**user_to_response(user))

    def ExporterDonneesUtilisateur(
        self, request: pb.UserIdRequest, context: grpc.ServicerContext
    ) -> pb.ExportDonneesUtilisateurResponse:
        json_export = json.dumps(self.export_service.exporter(request.user_id), ensure_ascii=False, indent=2)
        return pb.ExportDonneesUtilisateurResponse(json_export=json_export)

    def ResetUserPassword(self, request: pb.UserIdRequest, context: grpc.ServicerContext) -> pb.UserResponse:
        user = self.user_admin_service.resend_credentials(request.user_id)
        return pb.UserResponse(**user_to_response(user))

    def ListUsers(self, request: pb.EmptyRequest, context: grpc.ServicerContext) -> pb.ListUsersResponse:
        users = self.user_admin_service.list_users()
        return pb.ListUsersResponse(users=[pb.UserResponse(**user_to_response(u)) for u in users])

    def GetUser(self, request: pb.UserIdRequest, context: grpc.ServicerContext) -> pb.UserResponse:
        user = self.user_admin_service.get_user(request.user_id)
        return pb.UserResponse(**user_to_response(user))

    def RequestPasswordReset(self, request: pb.EmailRequest, context: grpc.ServicerContext) -> pb.StatusResponse:
        self.password_setup_service.request_password_reset(request.email)
        return pb.StatusResponse(success=True, message="Si ce compte existe, un e-mail a été envoyé")

    def SetPasswordWithToken(self, request: pb.SetPasswordRequest, context: grpc.ServicerContext) -> pb.StatusResponse:
        self.password_setup_service.set_password_with_token(request.token, request.new_password)
        return pb.StatusResponse(success=True, message="Mot de passe défini")

    def RequestPhoneOtp(self, request: pb.PhoneRequest, context: grpc.ServicerContext) -> pb.StatusResponse:
        self.phone_otp_service.request_otp_by_phone(request.phone_number)
        return pb.StatusResponse(success=True, message="Si ce numéro est enregistré, un code a été envoyé par WhatsApp")

    def VerifyOtpAndSetPassword(self, request: pb.VerifyOtpRequest, context: grpc.ServicerContext) -> pb.StatusResponse:
        self.phone_otp_service.verify_otp_and_set_password(
            phone_number=request.phone_number,
            otp_code=request.otp_code,
            new_password=request.new_password,
        )
        return pb.StatusResponse(success=True, message="Mot de passe défini")


def serve() -> None:
    server = grpc.server(
        futures.ThreadPoolExecutor(max_workers=10),
        interceptors=[
            AuthServerInterceptor(settings.INTERNAL_GRPC_KEY),
            ErrorHandlingInterceptor(),
            IdentityInterceptor(),
        ],
    )
    pb_grpc.add_AuthServiceServicer_to_server(AuthServiceServicer(), server)
    ouvrir_port_grpc(server, settings.AUTH_GRPC_PORT)
    server.start()
    print(f"Auth gRPC server démarré sur le port {settings.AUTH_GRPC_PORT}")
    server.wait_for_termination()
