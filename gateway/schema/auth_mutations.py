import strawberry
from strawberry import ID

from schema.context import (
    AuthError,
    clear_refresh_token_cookie,
    extract_refresh_token,
    extract_token,
    require_role,
    set_refresh_token_cookie,
)
from schema.auth_types import AuthPayload, OtpSentPayload, Role, User, _mask_phone, user_from_grpc
from schema.grpc_clients import auth_client


def _auth_payload_from_tokens(token_response) -> AuthPayload:
    user_response = auth_client.get_user(auth_client.validate_token(token_response.access_token).user_id)
    return AuthPayload(
        access_token=token_response.access_token,
        expires_in=token_response.expires_in,
        user=user_from_grpc(user_response),
    )


def _set_password_with_token(token: str, password: str) -> bool:
    # Activation de compte et réinitialisation de mot de passe partagent le
    # même mécanisme côté auth-service (voir SetPasswordWithToken) ; deux
    # noms de mutation distincts ici pour rester clair côté frontend.
    response = auth_client.set_password_with_token(token, password)
    return response.success


@strawberry.type
class AuthMutations:
    @strawberry.mutation(description="Connexion par nom d'utilisateur ou numéro de téléphone (+237XXXXXXXXX).")
    def login(self, info: strawberry.types.Info, identifier: str, password: str) -> AuthPayload:
        token_response = auth_client.login(identifier, password)
        set_refresh_token_cookie(info.context["response"], token_response.refresh_token)
        return _auth_payload_from_tokens(token_response)

    @strawberry.mutation
    def refresh_token(self, info: strawberry.types.Info) -> AuthPayload:
        refresh_token = extract_refresh_token(info.context["request"])
        if not refresh_token:
            raise AuthError("Refresh token manquant")

        token_response = auth_client.refresh_token(refresh_token)
        set_refresh_token_cookie(info.context["response"], token_response.refresh_token)
        return _auth_payload_from_tokens(token_response)

    @strawberry.mutation
    def logout(self, info: strawberry.types.Info) -> bool:
        token = extract_token(info.context["request"])
        if not token:
            raise AuthError("Authentification requise")
        response = auth_client.logout(token)
        clear_refresh_token_cookie(info.context["response"])
        return response.success

    @strawberry.mutation(
        description=(
            "Crée un utilisateur. ADMIN : email + téléphone requis, activation par e-mail. "
            "Autres rôles : téléphone requis, activation par OTP WhatsApp (email ignoré)."
        )
    )
    def create_user(
        self,
        info: strawberry.types.Info,
        username: str,
        phone_number: str,
        role: Role,
        email: str = "",
    ) -> User:
        require_role(info, "ADMIN")
        user_response = auth_client.create_user(
            username=username,
            email=email,
            phone_number=phone_number,
            role=role.value,
        )
        return user_from_grpc(user_response)

    @strawberry.mutation
    def update_user(
        self,
        info: strawberry.types.Info,
        id: ID,
        email: str = "",
        role: Role | None = None,
        phone_number: str = "",
    ) -> User:
        require_role(info, "ADMIN")
        user_response = auth_client.update_user(
            user_id=str(id),
            email=email,
            role=role.value if role else "",
            phone_number=phone_number,
        )
        return user_from_grpc(user_response)

    @strawberry.mutation
    def deactivate_user(self, info: strawberry.types.Info, id: strawberry.ID) -> User:
        require_role(info, "ADMIN")
        user_response = auth_client.deactivate_user(str(id))
        return user_from_grpc(user_response)

    @strawberry.mutation(description="Reset de mot de passe par e-mail — ADMIN uniquement.")
    def request_password_reset(self, email: str) -> bool:
        # Toujours true, qu'un compte existe ou non (ne révèle pas l'existence d'un compte).
        response = auth_client.request_password_reset(email)
        return response.success

    @strawberry.mutation
    def activate_account(self, token: str, password: str) -> bool:
        return _set_password_with_token(token, password)

    @strawberry.mutation
    def reset_password(self, token: str, password: str) -> bool:
        return _set_password_with_token(token, password)

    @strawberry.mutation(
        description=(
            "Envoie un code OTP à 6 chiffres par WhatsApp. "
            "Utilisé pour l'activation initiale et le reset de mot de passe par téléphone. "
            "Retourne toujours succès (ne révèle pas si le numéro est enregistré)."
        )
    )
    def request_phone_otp(self, phone_number: str) -> OtpSentPayload:
        auth_client.request_phone_otp(phone_number)
        return OtpSentPayload(masked_phone=_mask_phone(phone_number))

    @strawberry.mutation(description="Vérifie le code OTP WhatsApp et définit le nouveau mot de passe.")
    def verify_otp_and_set_password(self, phone_number: str, otp_code: str, password: str) -> bool:
        response = auth_client.verify_otp_and_set_password(
            phone_number=phone_number,
            otp_code=otp_code,
            new_password=password,
        )
        return response.success
