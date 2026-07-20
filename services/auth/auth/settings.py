"""Configuration Django du Auth Service."""

import sys
from datetime import timedelta
from pathlib import Path

import environ

BASE_DIR = Path(__file__).resolve().parent.parent
TESTING = "test" in sys.argv

env = environ.Env()
environ.Env.read_env(BASE_DIR / ".env")

SECRET_KEY = env("DJANGO_SECRET_KEY")
DEBUG = env.bool("DJANGO_DEBUG", default=False)
ALLOWED_HOSTS = env.list("DJANGO_ALLOWED_HOSTS", default=["localhost", "127.0.0.1"])

INSTALLED_APPS = [
    "django.contrib.admin",
    "django.contrib.auth",
    "django.contrib.contenttypes",
    "django.contrib.sessions",
    "django.contrib.messages",
    "django.contrib.staticfiles",
    "rest_framework",
    "rest_framework_simplejwt",
    "comptes",
]

MIDDLEWARE = [
    "django.middleware.security.SecurityMiddleware",
    "django.contrib.sessions.middleware.SessionMiddleware",
    "django.middleware.common.CommonMiddleware",
    "django.middleware.csrf.CsrfViewMiddleware",
    "django.contrib.auth.middleware.AuthenticationMiddleware",
    "django.contrib.messages.middleware.MessageMiddleware",
    "django.middleware.clickjacking.XFrameOptionsMiddleware",
]

ROOT_URLCONF = "auth.urls"

TEMPLATES = [
    {
        "BACKEND": "django.template.backends.django.DjangoTemplates",
        "DIRS": [],
        "APP_DIRS": True,
        "OPTIONS": {
            "context_processors": [
                "django.template.context_processors.debug",
                "django.template.context_processors.request",
                "django.contrib.auth.context_processors.auth",
                "django.contrib.messages.context_processors.messages",
            ],
        },
    },
]

WSGI_APPLICATION = "auth.wsgi.application"

if TESTING:
    DATABASES = {"default": {"ENGINE": "django.db.backends.sqlite3", "NAME": ":memory:"}}
else:
    DATABASES = {
        "default": {
            "ENGINE": "django.db.backends.postgresql",
            "HOST": env("AUTH_DB_HOST", default="localhost"),
            "PORT": env("AUTH_DB_PORT", default="5432"),
            "NAME": env("AUTH_DB_NAME", default="auth_db"),
            "USER": env("AUTH_DB_USER", default="auth_user"),
            "PASSWORD": env("AUTH_DB_PASSWORD", default=""),
        }
    }

AUTH_USER_MODEL = "comptes.User"

AUTH_PASSWORD_VALIDATORS = [
    {"NAME": "django.contrib.auth.password_validation.UserAttributeSimilarityValidator"},
    {"NAME": "django.contrib.auth.password_validation.MinimumLengthValidator"},
    {"NAME": "django.contrib.auth.password_validation.CommonPasswordValidator"},
    {"NAME": "django.contrib.auth.password_validation.NumericPasswordValidator"},
]

LANGUAGE_CODE = "en-us"
TIME_ZONE = "UTC"
USE_I18N = True
USE_TZ = True

STATIC_URL = "static/"
DEFAULT_AUTO_FIELD = "django.db.models.BigAutoField"

# --- gRPC ---
AUTH_GRPC_PORT = env.int("AUTH_GRPC_PORT", default=50051)

# --- Redis (pub/sub : notifie la gateway des mutations utilisateur) ---
REDIS_URL = env("REDIS_URL", default="redis://localhost:6379/0")

# --- JWT (RS256 asymétrique) ---
# auth signe avec la clé PRIVÉE et valide avec la PUBLIQUE. La gateway et les
# autres services ne décodent jamais le token : ils délèguent la validation à
# auth via le RPC ValidateToken. Les clés vivent hors du dépôt (gitignorées) ;
# en test, une paire éphémère en mémoire évite toute dépendance à un fichier.
JWT_ACCESS_TOKEN_LIFETIME = timedelta(hours=env.int("JWT_ACCESS_TOKEN_EXPIRE_HOURS", default=24))
JWT_REFRESH_TOKEN_LIFETIME = timedelta(days=env.int("JWT_REFRESH_TOKEN_EXPIRE_DAYS", default=7))

if TESTING:
    from cryptography.hazmat.primitives import serialization
    from cryptography.hazmat.primitives.asymmetric import rsa

    _jwt_key = rsa.generate_private_key(public_exponent=65537, key_size=2048)
    JWT_PRIVATE_KEY = _jwt_key.private_bytes(
        serialization.Encoding.PEM,
        serialization.PrivateFormat.PKCS8,
        serialization.NoEncryption(),
    ).decode()
    JWT_PUBLIC_KEY = (
        _jwt_key.public_key()
        .public_bytes(
            serialization.Encoding.PEM,
            serialization.PublicFormat.SubjectPublicKeyInfo,
        )
        .decode()
    )
else:
    _priv = Path(env("JWT_PRIVATE_KEY_PATH", default=str(BASE_DIR / "keys" / "jwt_private.pem")))
    _pub = Path(env("JWT_PUBLIC_KEY_PATH", default=str(BASE_DIR / "keys" / "jwt_public.pem")))
    try:
        JWT_PRIVATE_KEY = _priv.read_text()
        JWT_PUBLIC_KEY = _pub.read_text()
    except FileNotFoundError as exc:
        from django.core.exceptions import ImproperlyConfigured

        raise ImproperlyConfigured(
            f"Clé JWT introuvable ({exc.filename}). Générez la paire RSA depuis "
            "la racine du backend : ./scripts/gen-jwt-keys.sh"
        ) from exc

# --- Sécurité ---
MAX_LOGIN_ATTEMPTS = env.int("MAX_LOGIN_ATTEMPTS", default=5)
LOCKOUT_DURATION_MINUTES = env.int("LOCKOUT_DURATION_MINUTES", default=15)
# Nombre de codes OTP erronés tolérés par token avant invalidation : borne le
# brute-force du code à 6 chiffres (10^6 combinaisons) sur la fenêtre de validité.
MAX_OTP_ATTEMPTS = env.int("MAX_OTP_ATTEMPTS", default=5)
BCRYPT_ROUNDS = env.int("BCRYPT_ROUNDS", default=12)

SIMPLE_JWT = {
    "ACCESS_TOKEN_LIFETIME": JWT_ACCESS_TOKEN_LIFETIME,
    "REFRESH_TOKEN_LIFETIME": JWT_REFRESH_TOKEN_LIFETIME,
    "ALGORITHM": "RS256",
    "SIGNING_KEY": JWT_PRIVATE_KEY,
    "VERIFYING_KEY": JWT_PUBLIC_KEY,
    "USER_ID_FIELD": "id",
    "USER_ID_CLAIM": "user_id",
}

# --- E-mail (Brevo — 300 e-mails/jour gratuits) ---
BREVO_API_KEY = env("BREVO_API_KEY", default="")
BREVO_SENDER_NAME = env("BREVO_SENDER_NAME", default="SGFE")
BREVO_SENDER_EMAIL = env("BREVO_SENDER_EMAIL", default="no-reply@sgfe.example.com")

# Lien envoyé dans l'e-mail d'activation/réinitialisation : le frontend Angular
# lit le token dans l'URL et appelle activateAccount/resetPassword.
FRONTEND_URL = env("FRONTEND_URL", default="http://localhost:4200")
PASSWORD_SETUP_TOKEN_VALIDITY_HOURS = env.int("PASSWORD_SETUP_TOKEN_VALIDITY_HOURS", default=48)

# --- WhatsApp OTP (whatsapp-web.js service) ---
# URL du service Node.js whatsapp-service (voir whatsapp-service/server.js)
WHATSAPP_SERVICE_URL = env("WHATSAPP_SERVICE_URL", default="http://whatsapp-service:3000")
# Clé partagée envoyée en en-tête X-Internal-Api-Key vers whatsapp-service.
WHATSAPP_INTERNAL_API_KEY = env("WHATSAPP_INTERNAL_API_KEY", default="")
# Durée de validité du code OTP en minutes
PHONE_OTP_VALIDITY_MINUTES = env.int("PHONE_OTP_VALIDITY_MINUTES", default=10)
