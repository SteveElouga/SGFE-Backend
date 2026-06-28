import secrets
import uuid

from django.contrib.auth.base_user import AbstractBaseUser, BaseUserManager
from django.contrib.auth.models import PermissionsMixin
from django.db import models
from django.utils import timezone


class Role(models.TextChoices):
    ADMIN = "ADMIN", "Admin"
    AGENT = "AGENT", "Agent"
    COMPTABLE = "COMPTABLE", "Comptable"
    SUPERVISEUR = "SUPERVISEUR", "Superviseur"


class UserManager(BaseUserManager):
    """Manager personnalisé : authentification par username, pas d'email obligatoire en USERNAME_FIELD."""

    def create_user(self, username: str, email: str, role: str, password: str | None = None, **extra_fields) -> "User":
        if not username:
            raise ValueError("Le username est obligatoire")
        user = self.model(username=username, email=self.normalize_email(email), role=role, **extra_fields)
        if password:
            user.set_password(password)
        else:
            # Compte créé par un admin : pas de mot de passe utilisable tant
            # que l'utilisateur ne l'a pas défini via le lien d'activation.
            user.set_unusable_password()
        user.save(using=self._db)
        return user

    def create_superuser(self, username: str, email: str, password: str, **extra_fields) -> "User":
        extra_fields.setdefault("role", Role.ADMIN)
        extra_fields.setdefault("is_staff", True)
        extra_fields.setdefault("is_superuser", True)
        return self.create_user(username, email, role=extra_fields.pop("role"), password=password, **extra_fields)


class User(AbstractBaseUser, PermissionsMixin):
    """Utilisateur du système, mappé sur la table `users` de auth_db (docs/ARCHITECTURE.md §8.1)."""

    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    username = models.CharField(max_length=100, unique=True)
    email = models.EmailField(max_length=255, unique=True)
    role = models.CharField(max_length=20, choices=Role.choices)
    is_active = models.BooleanField(default=True)
    failed_attempts = models.IntegerField(default=0)
    locked_until = models.DateTimeField(null=True, blank=True)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    is_staff = models.BooleanField(default=False)

    objects = UserManager()

    USERNAME_FIELD = "username"
    REQUIRED_FIELDS = ["email"]

    class Meta:
        db_table = "users"

    def __str__(self) -> str:
        return self.username


class RevokedToken(models.Model):
    """Blacklist des JWT révoqués (logout), mappée sur `revoked_tokens` (docs/ARCHITECTURE.md §8.1)."""

    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    token_jti = models.CharField(max_length=255, unique=True)
    revoked_at = models.DateTimeField(auto_now_add=True)
    expires_at = models.DateTimeField()

    class Meta:
        db_table = "revoked_tokens"


def _generate_token() -> str:
    return secrets.token_urlsafe(32)


class PasswordSetupToken(models.Model):
    """Token à usage unique pour l'activation de compte (premier mot de passe)
    et la réinitialisation de mot de passe — même mécanisme pour les deux,
    envoyé par e-mail (voir email_client.py)."""

    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    user = models.ForeignKey(User, on_delete=models.CASCADE, related_name="password_tokens")
    token = models.CharField(max_length=64, unique=True, default=_generate_token)
    created_at = models.DateTimeField(auto_now_add=True)
    expires_at = models.DateTimeField()
    used_at = models.DateTimeField(null=True, blank=True)

    class Meta:
        db_table = "password_setup_tokens"

    def is_valid(self) -> bool:
        return self.used_at is None and self.expires_at > timezone.now()

    def mark_used(self) -> None:
        self.used_at = timezone.now()
        self.save(update_fields=["used_at"])
