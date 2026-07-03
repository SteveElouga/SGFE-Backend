from django.db.models import Q

from comptes.models import PasswordSetupToken, PhoneOtpToken, RevokedToken, User


class UserRepository:
    """Accès base de données pour les utilisateurs."""

    def get_by_id(self, user_id: str) -> User:
        return User.objects.get(id=user_id)

    def get_by_username(self, username: str) -> User:
        return User.objects.get(username=username)

    def get_by_email(self, email: str) -> User:
        return User.objects.get(email=email)

    def get_by_phone(self, phone_number: str) -> User:
        return User.objects.get(phone_number=phone_number)

    def get_by_username_or_phone(self, identifier: str) -> User:
        """Résout un identifiant qui peut être un username ou un numéro de téléphone."""
        return User.objects.get(Q(username=identifier) | Q(phone_number=identifier))

    def list_all(self) -> list[User]:
        return list(User.objects.all().order_by("-created_at"))

    def count_active_admins(self, exclude_id: str | None = None) -> int:
        """Compte les administrateurs actifs, en excluant éventuellement un utilisateur."""
        qs = User.objects.filter(role="ADMIN", is_active=True)
        if exclude_id is not None:
            qs = qs.exclude(id=exclude_id)
        return qs.count()

    def create(self, username: str, phone_number: str, role: str, email: str | None = None) -> User:
        # is_active=False : le compte n'est activé qu'après définition du mot de passe
        # (via lien email pour ADMIN, ou OTP WhatsApp pour les autres rôles).
        return User.objects.create_user(
            username=username,
            email=email,
            phone_number=phone_number,
            role=role,
            is_active=False,
        )

    def save(self, user: User) -> User:
        user.save()
        return user


class RevokedTokenRepository:
    """Accès base de données pour la blacklist des tokens JWT."""

    def is_revoked(self, token_jti: str) -> bool:
        return RevokedToken.objects.filter(token_jti=token_jti).exists()

    def revoke(self, token_jti: str, expires_at) -> RevokedToken:
        return RevokedToken.objects.create(token_jti=token_jti, expires_at=expires_at)


class PasswordSetupTokenRepository:
    """Accès base de données pour les tokens d'activation/réinitialisation par e-mail (ADMIN)."""

    def create(self, user: User, expires_at) -> PasswordSetupToken:
        return PasswordSetupToken.objects.create(user=user, expires_at=expires_at)

    def get_valid(self, token: str) -> PasswordSetupToken:
        return PasswordSetupToken.objects.get(token=token)


class PhoneOtpTokenRepository:
    """Accès base de données pour les OTP WhatsApp."""

    def create(self, user: User, raw_otp: str, expires_at) -> PhoneOtpToken:
        otp_token = PhoneOtpToken(user=user, expires_at=expires_at)
        otp_token.set_otp(raw_otp)
        otp_token.save()
        return otp_token

    def get_latest_valid(self, user: User) -> PhoneOtpToken:
        """Retourne le dernier OTP valide pour cet utilisateur."""
        return PhoneOtpToken.objects.filter(user=user, used_at__isnull=True).order_by("-created_at").first()

    def invalidate_previous(self, user: User) -> None:
        """Invalide tous les OTP non consommés d'un utilisateur avant d'en créer un nouveau."""
        from django.utils import timezone

        PhoneOtpToken.objects.filter(user=user, used_at__isnull=True).update(used_at=timezone.now())
