from datetime import timedelta

from django.conf import settings
from django.contrib.auth.hashers import check_password
from django.core.exceptions import ObjectDoesNotExist
from django.utils import timezone
from rest_framework_simplejwt.exceptions import TokenError
from rest_framework_simplejwt.tokens import AccessToken, RefreshToken

from comptes.email_client import email_client
from comptes.models import User, _generate_otp
from comptes.repositories import (
    PasswordSetupTokenRepository,
    PhoneOtpTokenRepository,
    RevokedTokenRepository,
    UserRepository,
)
from comptes.validators import validate_phone_cameroon
from comptes.whatsapp_client import whatsapp_client


_MSG_INVALID_CREDENTIALS = "Identifiants invalides"


class AuthenticationError(Exception):
    """Échec d'authentification (identifiants invalides, compte verrouillé/inactif)."""


class AuthService:
    """Logique métier d'authentification et de gestion des tokens JWT."""

    def __init__(self) -> None:
        self.users = UserRepository()
        self.revoked_tokens = RevokedTokenRepository()

    def login(self, identifier: str, password: str) -> tuple[str, str, int]:
        """Authentifie un utilisateur par son username ou son numéro de téléphone."""
        try:
            user = self.users.get_by_username_or_phone(identifier)
        except ObjectDoesNotExist as exc:
            raise AuthenticationError(_MSG_INVALID_CREDENTIALS) from exc

        if user.locked_until and user.locked_until > timezone.now():
            raise AuthenticationError("Compte verrouillé temporairement")

        if not user.is_active:
            raise AuthenticationError("Compte désactivé")

        if not check_password(password, user.password):
            self._enregistrer_echec(user)
            raise AuthenticationError(_MSG_INVALID_CREDENTIALS)

        user.failed_attempts = 0
        user.locked_until = None
        self.users.save(user)

        return self._generer_tokens(user)

    def _enregistrer_echec(self, user: User) -> None:
        user.failed_attempts += 1
        if user.failed_attempts >= settings.MAX_LOGIN_ATTEMPTS:
            user.locked_until = timezone.now() + timedelta(minutes=settings.LOCKOUT_DURATION_MINUTES)
        self.users.save(user)

    def _generer_tokens(self, user: User) -> tuple[str, str, int]:
        refresh = RefreshToken.for_user(user)
        refresh["role"] = user.role
        access = refresh.access_token
        access["role"] = user.role
        expires_in = int(settings.JWT_ACCESS_TOKEN_LIFETIME.total_seconds())
        return str(access), str(refresh), expires_in

    def validate_token(self, token: str) -> User:
        try:
            access = AccessToken(token)
        except TokenError as exc:
            raise AuthenticationError("Token invalide ou expiré") from exc

        if self.revoked_tokens.is_revoked(access["jti"]):
            raise AuthenticationError("Token révoqué")

        try:
            return self.users.get_by_id(access["user_id"])
        except ObjectDoesNotExist as exc:
            raise AuthenticationError("Utilisateur introuvable") from exc

    def refresh_token(self, refresh_token: str) -> tuple[str, str, int]:
        try:
            refresh = RefreshToken(refresh_token)
        except TokenError as exc:
            raise AuthenticationError("Refresh token invalide ou expiré") from exc

        if self.revoked_tokens.is_revoked(refresh["jti"]):
            raise AuthenticationError("Refresh token révoqué")

        try:
            user = self.users.get_by_id(refresh["user_id"])
        except ObjectDoesNotExist as exc:
            raise AuthenticationError("Utilisateur introuvable") from exc

        return self._generer_tokens(user)

    def logout(self, token: str) -> None:
        try:
            access = AccessToken(token)
        except TokenError as exc:
            raise AuthenticationError("Token invalide") from exc

        self.revoked_tokens.revoke(
            token_jti=access["jti"],
            expires_at=timezone.datetime.fromtimestamp(access["exp"], tz=timezone.get_current_timezone()),
        )


class UserAdminService:
    """Gestion CRUD des utilisateurs (réservée au rôle ADMIN, vérifié côté appelant)."""

    def __init__(self) -> None:
        self.users = UserRepository()
        self.password_setup = PasswordSetupService()
        self.phone_otp = PhoneOtpService()

    def create_user(self, username: str, phone_number: str, role: str, email: str = "") -> User:
        """Crée un utilisateur et déclenche le flux d'activation adapté au rôle.

        ADMIN : activation par e-mail (lien de définition du mot de passe).
        Autres rôles : activation par OTP WhatsApp.
        """
        phone = validate_phone_cameroon(phone_number)

        if role == "ADMIN":
            if not email:
                raise ValueError("L'e-mail est obligatoire pour le rôle ADMIN")
            user = self.users.create(username=username, email=email, phone_number=phone, role=role)
            self.password_setup.send_activation_email(user)
        else:
            user = self.users.create(username=username, phone_number=phone, role=role)
            self.phone_otp.send_otp(user)

        return user

    def update_user(self, user_id: str, email: str, role: str, phone_number: str = "") -> User:
        user = self.users.get_by_id(user_id)
        if email:
            user.email = email
        if role:
            user.role = role
        if phone_number:
            user.phone_number = validate_phone_cameroon(phone_number)
        return self.users.save(user)

    def deactivate_user(self, user_id: str) -> User:
        user = self.users.get_by_id(user_id)
        user.is_active = False
        return self.users.save(user)

    def get_user(self, user_id: str) -> User:
        return self.users.get_by_id(user_id)

    def list_users(self) -> list[User]:
        return self.users.list_all()


class PasswordSetupService:
    """Activation de compte et réinitialisation de mot de passe par e-mail (ADMIN uniquement).

    Les deux flux partagent le même mécanisme : un token à usage unique,
    limité dans le temps, envoyé par e-mail, qui permet de définir un
    nouveau mot de passe.
    """

    def __init__(self) -> None:
        self.users = UserRepository()
        self.tokens = PasswordSetupTokenRepository()

    def _create_token_and_send(self, user: User, subject: str, intro_html: str) -> None:
        expires_at = timezone.now() + timedelta(hours=settings.PASSWORD_SETUP_TOKEN_VALIDITY_HOURS)
        setup_token = self.tokens.create(user=user, expires_at=expires_at)
        link = f"{settings.FRONTEND_URL}/set-password?token={setup_token.token}"
        email_client.send(
            to_email=user.email,
            to_name=user.username,
            subject=subject,
            html_content=f'{intro_html}<p><a href="{link}">{link}</a></p>'
            f"<p>Ce lien expire dans {settings.PASSWORD_SETUP_TOKEN_VALIDITY_HOURS} heures.</p>",
        )

    def send_activation_email(self, user: User) -> None:
        self._create_token_and_send(
            user,
            subject="Activez votre compte SGFE",
            intro_html=f"<p>Bonjour {user.username},</p><p>Votre compte a été créé. Définissez votre mot de passe :</p>",
        )

    def request_password_reset(self, email: str) -> None:
        # Ne jamais révéler si l'e-mail existe ou non : succès silencieux dans les deux cas.
        try:
            user = self.users.get_by_email(email)
        except ObjectDoesNotExist:
            return
        # Seul l'ADMIN peut réinitialiser par e-mail
        if user.role != "ADMIN":
            return
        self._create_token_and_send(
            user,
            subject="Réinitialisation de votre mot de passe SGFE",
            intro_html=f"<p>Bonjour {user.username},</p><p>Cliquez sur le lien pour définir un nouveau mot de passe :</p>",
        )

    def set_password_with_token(self, token: str, new_password: str) -> None:
        try:
            setup_token = self.tokens.get_valid(token)
        except ObjectDoesNotExist as exc:
            raise AuthenticationError("Token invalide") from exc

        if not setup_token.is_valid():
            raise AuthenticationError("Token invalide ou expiré")

        user = setup_token.user
        user.set_password(new_password)
        self.users.save(user)
        setup_token.mark_used()


class PhoneOtpService:
    """Activation de compte et réinitialisation de mot de passe par OTP WhatsApp.

    Utilisé pour tous les rôles (ADMIN peut aussi passer par ce flux en plus de l'e-mail).
    L'OTP est valide pendant PHONE_OTP_VALIDITY_MINUTES minutes, à usage unique.
    """

    def __init__(self) -> None:
        self.users = UserRepository()
        self.otp_tokens = PhoneOtpTokenRepository()

    def send_otp(self, user: User) -> None:
        """Génère un OTP, invalide les précédents, et l'envoie par WhatsApp."""
        self.otp_tokens.invalidate_previous(user)
        raw_otp = _generate_otp()
        expires_at = timezone.now() + timedelta(minutes=settings.PHONE_OTP_VALIDITY_MINUTES)
        self.otp_tokens.create(user=user, raw_otp=raw_otp, expires_at=expires_at)
        whatsapp_client.send(
            to_phone=user.phone_number,
            message=f"Votre code SGFE : {raw_otp}\nValable {settings.PHONE_OTP_VALIDITY_MINUTES} minutes. Ne le partagez jamais.",
        )

    def request_otp_by_phone(self, phone_number: str) -> None:
        """Point d'entrée public : résout l'utilisateur par téléphone et envoie un OTP.

        Succès silencieux si le numéro n'existe pas (ne révèle pas les comptes).
        """
        phone = validate_phone_cameroon(phone_number)
        try:
            user = self.users.get_by_phone(phone)
        except ObjectDoesNotExist:
            return
        if not user.is_active:
            return
        self.send_otp(user)

    def verify_otp_and_set_password(self, phone_number: str, otp_code: str, new_password: str) -> None:
        """Vérifie l'OTP et définit le nouveau mot de passe si valide."""
        phone = validate_phone_cameroon(phone_number)

        try:
            user = self.users.get_by_phone(phone)
        except ObjectDoesNotExist as exc:
            raise AuthenticationError(_MSG_INVALID_CREDENTIALS) from exc

        otp_token = self.otp_tokens.get_latest_valid(user)
        if otp_token is None or not otp_token.is_valid():
            raise AuthenticationError("Code OTP invalide ou expiré")

        if not otp_token.check_otp(otp_code):
            raise AuthenticationError("Code OTP invalide ou expiré")

        user.set_password(new_password)
        self.users.save(user)
        otp_token.mark_used()
