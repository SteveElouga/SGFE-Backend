"""Configuration Django du Auth Service."""

import logging
import sys
import time
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

# La CI fait tourner ces tests sur PostgreSQL 16 (même moteur qu'en prod) en
# positionnant FORCE_POSTGRES_TESTS=True (+ les AUTH_DB_* habituels pointés
# vers le service postgres du job). Par défaut (dev local), TESTING seul
# suffit à retomber sur SQLite en mémoire — rapide, zéro dépendance.
if TESTING and not env.bool("FORCE_POSTGRES_TESTS", default=False):
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

LANGUAGE_CODE = "fr-fr"
TIME_ZONE = "UTC"
USE_I18N = True
USE_TZ = True

# Catalogue de traduction des messages utilisateur (voir CLAUDE.md racine,
# section i18n) : `locale/` reste vide tant qu'aucun `django.po` n'est généré.
LOCALE_PATHS = [BASE_DIR / "locale"]

STATIC_URL = "static/"
DEFAULT_AUTO_FIELD = "django.db.models.BigAutoField"

# --- gRPC ---
AUTH_GRPC_PORT = env.int("AUTH_GRPC_PORT", default=50051)

# Authentification de la couche gRPC interne (registre, point 1).
#
# Secret partagé entre tous les services. Sans lui, le serveur gRPC refuse de
# démarrer — même en développement : une valeur par défaut silencieuse
# recréerait exactement le trou qu'on ferme, un contrôle qui a l'air posé et
# ne protège rien.
INTERNAL_GRPC_KEY = env("INTERNAL_GRPC_KEY", default="")


# --- Redis (pub/sub : notifie la gateway des mutations utilisateur) ---
REDIS_URL = env("REDIS_URL", default="redis://localhost:6379/0")

# --- JWT (RS256 asymétrique) ---
# auth signe avec la clé PRIVÉE et valide avec la PUBLIQUE. La gateway et les
# autres services ne décodent jamais le token : ils délèguent la validation à
# auth via le RPC ValidateToken. Les clés vivent hors du dépôt (gitignorées) ;
# en test, une paire éphémère en mémoire évite toute dépendance à un fichier.
# Access token court (15 min par défaut) : réduit fortement la fenêtre d'exploitation
# d'un token volé. Le refresh token (cookie HttpOnly, 7 j) le renouvelle en silence —
# le front rejoue toute requête GraphQL sur UNAUTHENTICATED (auth-error.link). Réglable
# via l'env (JWT_ACCESS_TOKEN_EXPIRE_MINUTES) sans changer le code.
JWT_ACCESS_TOKEN_LIFETIME = timedelta(minutes=env.int("JWT_ACCESS_TOKEN_EXPIRE_MINUTES", default=15))
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

# Mots de passe : hacheur par défaut de Django (PBKDF2-HMAC-SHA256, ~600k itérations)
# — robuste et sans dépendance native. L'ancien BCRYPT_ROUNDS a été retiré : jamais
# câblé à un PASSWORD_HASHERS, il n'avait aucun effet (les mots de passe étaient déjà
# hachés en PBKDF2). Pour passer à bcrypt/argon2, définir explicitement PASSWORD_HASHERS.

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
#
# En dev, l'URL est connue d'avance (port fixe du `ng serve` de ce dépot) :
# aucune configuration à fournir. En production, elle dépend du domaine réel
# de déploiement et ne peut pas être devinée — un défaut silencieux sur
# localhost enverrait des liens cassés à de vrais utilisateurs. `env(...)`
# sans `default` lève `ImproperlyConfigured` si la variable manque : le
# service refuse de démarrer plutôt que d'envoyer un lien mort.
FRONTEND_URL = env("FRONTEND_URL", default="http://localhost:4321") if DEBUG else env("FRONTEND_URL")
PASSWORD_SETUP_TOKEN_VALIDITY_HOURS = env.int("PASSWORD_SETUP_TOKEN_VALIDITY_HOURS", default=48)

# --- WhatsApp OTP (whatsapp-web.js service) ---
# URL du service Node.js whatsapp-service (voir whatsapp-service/server.js)
WHATSAPP_SERVICE_URL = env("WHATSAPP_SERVICE_URL", default="http://whatsapp-service:3000")
# Clé partagée envoyée en en-tête X-Internal-Api-Key vers whatsapp-service.
WHATSAPP_INTERNAL_API_KEY = env("WHATSAPP_INTERNAL_API_KEY", default="")
# Durée de validité du code OTP en minutes
PHONE_OTP_VALIDITY_MINUTES = env.int("PHONE_OTP_VALIDITY_MINUTES", default=10)


# --- Journalisation (voir AUDIT_SGFE.md §J : rétention + horodatage fiable) ---
#
# Horodatage UTC explicite : `logging.Formatter.converter` est basculé sur
# `time.gmtime` pour tout le processus (cohérent avec `TIME_ZONE = "UTC"`
# déjà en vigueur) — des journaux de plusieurs conteneurs qui ne s'accordent
# pas sur l'heure ne sont pas exploitables comme preuve. Rétention
# configurable via `LOG_RETENTION_DAYS` (défaut 30 jours) :
# `TimedRotatingFileHandler` tourne un fichier par jour et purge au-delà.
#
# Hors périmètre ici (item observabilité séparé, non entamé — voir
# AUDIT_SGFE.md §I) : un vrai `trace_id` de corrélation cross-service.
logging.Formatter.converter = time.gmtime

LOG_RETENTION_DAYS = env.int("LOG_RETENTION_DAYS", default=30)
LOG_DIR = Path(env("LOG_DIR", default=str(BASE_DIR / "logs")))

_LOGGING_HANDLERS: list[str] = ["console"]
_LOGGING_HANDLER_CONFIG: dict[str, dict[str, object]] = {
    "console": {
        "class": "logging.StreamHandler",
        "formatter": "iso8601",
    },
}
# Pas de fichier pendant les tests : évite d'écrire sur disque à chaque
# `manage.py test`, comme le reste du dépôt qui bascule sur SQLite/tmpdir en
# mode TESTING plutôt que de toucher un état persistant.
if not TESTING:
    LOG_DIR.mkdir(parents=True, exist_ok=True)
    _LOGGING_HANDLERS.append("file")
    _LOGGING_HANDLER_CONFIG["file"] = {
        "class": "logging.handlers.TimedRotatingFileHandler",
        "filename": str(LOG_DIR / "auth.log"),
        "when": "midnight",
        "utc": True,
        "backupCount": LOG_RETENTION_DAYS,
        "formatter": "iso8601",
    }

LOGGING: dict[str, object] = {
    "version": 1,
    "disable_existing_loggers": False,
    "formatters": {
        "iso8601": {
            "format": "%(asctime)s.%(msecs)03dZ %(levelname)s %(name)s %(message)s",
            "datefmt": "%Y-%m-%dT%H:%M:%S",
        },
    },
    "handlers": _LOGGING_HANDLER_CONFIG,
    "root": {
        "handlers": _LOGGING_HANDLERS,
        "level": env("DJANGO_LOG_LEVEL", default="INFO"),
    },
}
