import secrets
import uuid
from typing import cast

from django.contrib.auth.base_user import AbstractBaseUser, BaseUserManager
from django.contrib.auth.hashers import check_password, make_password
from django.contrib.auth.models import PermissionsMixin
from django.db import models
from django.utils import timezone


class Role(models.TextChoices):
    ADMIN = "ADMIN", "Admin"
    AGENT = "AGENT", "Agent"
    COMPTABLE = "COMPTABLE", "Comptable"
    SUPERVISEUR = "SUPERVISEUR", "Superviseur"


class UserManager(BaseUserManager["User"]):
    """Manager personnalisé : authentification par username, pas d'email obligatoire en USERNAME_FIELD."""

    def create_user(
        self,
        username: str,
        email: str | None = None,
        role: str | None = None,
        password: str | None = None,
        **extra_fields: bool | str,
    ) -> "User":
        if not username:
            raise ValueError("Le username est obligatoire")
        if not role:
            raise ValueError("Le rôle est obligatoire")
        normalized_email = self.normalize_email(email) if email else None
        user: User = self.model(username=username, email=normalized_email, role=role, **extra_fields)
        if password:
            user.set_password(password)
        else:
            # Compte créé par un admin : pas de mot de passe utilisable tant
            # que l'utilisateur ne l'a pas défini via le lien d'activation.
            user.set_unusable_password()
        user.save(using=self._db)
        return user

    def create_superuser(self, username: str, email: str, password: str, **extra_fields: bool | str) -> "User":
        extra_fields.setdefault("role", Role.ADMIN)
        extra_fields.setdefault("is_staff", True)
        extra_fields.setdefault("is_superuser", True)
        role = cast(str, extra_fields.pop("role"))
        return self.create_user(username, email, role=role, password=password, **extra_fields)


class User(AbstractBaseUser, PermissionsMixin):
    """Utilisateur du système, mappé sur la table `users` de auth_db (docs/ARCHITECTURE.md §8.1)."""

    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    username = models.CharField(max_length=100, unique=True)
    # Obligatoire pour ADMIN (activation e-mail + reset e-mail). Vide pour les autres rôles.
    email = models.EmailField(max_length=255, unique=True, null=True, blank=True)
    # Obligatoire pour tous les rôles (activation et reset par OTP WhatsApp).
    phone_number = models.CharField(max_length=20, unique=True)
    role = models.CharField(max_length=20, choices=Role.choices)
    is_active = models.BooleanField(default=True)
    failed_attempts = models.IntegerField(default=0)
    locked_until = models.DateTimeField(null=True, blank=True)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    # Instant du dernier changement de mot de passe.
    #
    # La liste noire des jetons révoque un jeton nommément — elle ne peut donc
    # pas fermer *toutes* les sessions d'une personne, puisque les jetons émis
    # ne sont stockés nulle part. Or c'est exactement ce qu'attend quelqu'un qui
    # change son mot de passe parce qu'il pense que son compte est compromis :
    # sans cela, la session de l'intrus continue de fonctionner, et le geste de
    # défense ne défend rien.
    #
    # Un horodatage suffit : tout jeton émis avant lui est refusé. Aucun stockage
    # par session, et la révocation vaut pour les jetons d'accès comme pour ceux
    # de rafraîchissement, sur tous les appareils à la fois.
    password_changed_at = models.DateTimeField(null=True, blank=True)

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


def _generate_otp() -> str:
    """Génère un code OTP à 6 chiffres cryptographiquement sûr."""
    return f"{secrets.randbelow(1_000_000):06d}"


class PhoneOtpToken(models.Model):
    """OTP à 6 chiffres envoyé par WhatsApp pour l'activation de compte
    et la réinitialisation de mot de passe des utilisateurs non-ADMIN
    (et optionnellement ADMIN via téléphone).

    Le code est stocké haché — jamais en clair.
    """

    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    user = models.ForeignKey(User, on_delete=models.CASCADE, related_name="phone_otp_tokens")
    # Stocké haché via Django's make_password (bcrypt/pbkdf2 selon config)
    otp_hash = models.CharField(max_length=255)
    created_at = models.DateTimeField(auto_now_add=True)
    expires_at = models.DateTimeField()
    used_at = models.DateTimeField(null=True, blank=True)
    # Nombre de codes erronés soumis pour ce token. Au-delà de
    # settings.MAX_OTP_ATTEMPTS le token est invalidé, ce qui empêche le
    # brute-force du code à 6 chiffres (le login dispose d'un verrou équivalent
    # via User.failed_attempts / locked_until).
    attempts = models.IntegerField(default=0)

    class Meta:
        db_table = "phone_otp_tokens"

    def set_otp(self, raw_otp: str) -> None:
        self.otp_hash = make_password(raw_otp)

    def check_otp(self, raw_otp: str) -> bool:
        return check_password(raw_otp, self.otp_hash)

    def is_valid(self) -> bool:
        return self.used_at is None and self.expires_at > timezone.now()

    def mark_used(self) -> None:
        self.used_at = timezone.now()
        self.save(update_fields=["used_at"])

    def register_failed_attempt(self, max_attempts: int) -> None:
        """Comptabilise un code erroné ; invalide le token dès le plafond atteint."""
        self.attempts += 1
        if self.attempts >= max_attempts:
            self.used_at = timezone.now()
        self.save(update_fields=["attempts", "used_at"])
