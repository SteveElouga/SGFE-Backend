import uuid

from django.contrib.auth.base_user import AbstractBaseUser, BaseUserManager
from django.contrib.auth.models import PermissionsMixin
from django.db import models


class Role(models.TextChoices):
    ADMIN = "ADMIN", "Admin"
    AGENT = "AGENT", "Agent"
    COMPTABLE = "COMPTABLE", "Comptable"


class UserManager(BaseUserManager):
    """Manager personnalisé : authentification par username, pas d'email obligatoire en USERNAME_FIELD."""

    def create_user(self, username: str, email: str, password: str, role: str, **extra_fields) -> "User":
        if not username:
            raise ValueError("Le username est obligatoire")
        user = self.model(username=username, email=self.normalize_email(email), role=role, **extra_fields)
        user.set_password(password)
        user.save(using=self._db)
        return user

    def create_superuser(self, username: str, email: str, password: str, **extra_fields) -> "User":
        extra_fields.setdefault("role", Role.ADMIN)
        extra_fields.setdefault("is_staff", True)
        extra_fields.setdefault("is_superuser", True)
        return self.create_user(username, email, password, **extra_fields)


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
