from comptes.models import PasswordSetupToken, RevokedToken, User


class UserRepository:
    """Accès base de données pour les utilisateurs."""

    def get_by_id(self, user_id: str) -> User:
        return User.objects.get(id=user_id)

    def get_by_username(self, username: str) -> User:
        return User.objects.get(username=username)

    def get_by_email(self, email: str) -> User:
        return User.objects.get(email=email)

    def list_all(self) -> list[User]:
        return list(User.objects.all().order_by("-created_at"))

    def create(self, username: str, email: str, role: str) -> User:
        # Pas de mot de passe à la création : voir PasswordSetupToken (activation par e-mail).
        return User.objects.create_user(username=username, email=email, role=role)

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
    """Accès base de données pour les tokens d'activation/réinitialisation de mot de passe."""

    def create(self, user: User, expires_at) -> PasswordSetupToken:
        return PasswordSetupToken.objects.create(user=user, expires_at=expires_at)

    def get_valid(self, token: str) -> PasswordSetupToken:
        return PasswordSetupToken.objects.get(token=token)
