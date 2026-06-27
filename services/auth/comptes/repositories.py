from comptes.models import RevokedToken, User


class UserRepository:
    """Accès base de données pour les utilisateurs."""

    def get_by_id(self, user_id: str) -> User:
        return User.objects.get(id=user_id)

    def get_by_username(self, username: str) -> User:
        return User.objects.get(username=username)

    def list_all(self) -> list[User]:
        return list(User.objects.all().order_by("-created_at"))

    def create(self, username: str, email: str, password: str, role: str) -> User:
        return User.objects.create_user(username=username, email=email, password=password, role=role)

    def save(self, user: User) -> User:
        user.save()
        return user


class RevokedTokenRepository:
    """Accès base de données pour la blacklist des tokens JWT."""

    def is_revoked(self, token_jti: str) -> bool:
        return RevokedToken.objects.filter(token_jti=token_jti).exists()

    def revoke(self, token_jti: str, expires_at) -> RevokedToken:
        return RevokedToken.objects.create(token_jti=token_jti, expires_at=expires_at)
