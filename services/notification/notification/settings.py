"""Configuration Django du Notification Service."""

import logging
import sys
import time
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
    "notifications",
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

ROOT_URLCONF = "notification.urls"

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

WSGI_APPLICATION = "notification.wsgi.application"

# La CI fait tourner ces tests sur PostgreSQL 16 (même moteur qu'en prod) en
# positionnant FORCE_POSTGRES_TESTS=True (+ les NOTIFICATION_DB_* habituels
# pointés vers le service postgres du job). Par défaut (dev local), TESTING
# seul suffit à retomber sur SQLite en mémoire — rapide, zéro dépendance.
if TESTING and not env.bool("FORCE_POSTGRES_TESTS", default=False):
    DATABASES = {"default": {"ENGINE": "django.db.backends.sqlite3", "NAME": ":memory:"}}
else:
    DATABASES = {
        "default": {
            "ENGINE": "django.db.backends.postgresql",
            "HOST": env("NOTIFICATION_DB_HOST", default="localhost"),
            "PORT": env("NOTIFICATION_DB_PORT", default="5432"),
            "NAME": env("NOTIFICATION_DB_NAME", default="notification_db"),
            "USER": env("NOTIFICATION_DB_USER", default="notification_user"),
            "PASSWORD": env("NOTIFICATION_DB_PASSWORD", default=""),
        }
    }

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
NOTIFICATION_GRPC_PORT = env.int("NOTIFICATION_GRPC_PORT", default=50056)

# --- Clients gRPC vers autres services ---
FACTURATION_GRPC_HOST = env("FACTURATION_GRPC_HOST", default="localhost")
FACTURATION_GRPC_PORT = env.int("FACTURATION_GRPC_PORT", default=50054)

ABONNE_GRPC_HOST = env("ABONNE_GRPC_HOST", default="localhost")
ABONNE_GRPC_PORT = env.int("ABONNE_GRPC_PORT", default=50052)

PAIEMENT_GRPC_HOST = env("PAIEMENT_GRPC_HOST", default="localhost")
PAIEMENT_GRPC_PORT = env.int("PAIEMENT_GRPC_PORT", default=50055)

CONFIG_GRPC_HOST = env("CONFIG_GRPC_HOST", default="localhost")
CONFIG_GRPC_PORT = env.int("CONFIG_GRPC_PORT", default=50058)

# Authentification de la couche gRPC interne (registre, point 1).
#
# Secret partagé entre tous les services. Sans lui, le serveur gRPC refuse de
# démarrer — même en développement : une valeur par défaut silencieuse
# recréerait exactement le trou qu'on ferme, un contrôle qui a l'air posé et
# ne protège rien.
INTERNAL_GRPC_KEY = env("INTERNAL_GRPC_KEY", default="")


# --- WhatsApp (whatsapp-web.js service) ---
WHATSAPP_SERVICE_URL = env("WHATSAPP_SERVICE_URL", default="http://localhost:3000")
# Clé partagée envoyée en en-tête X-Internal-Api-Key vers whatsapp-service.
WHATSAPP_INTERNAL_API_KEY = env("WHATSAPP_INTERNAL_API_KEY", default="")

# --- Frontend (pour les liens tokenisés) ---
# En dev, l'URL est connue d'avance (port fixe du `ng serve` de ce dépot) :
# aucune configuration à fournir. En production, elle dépend du domaine réel
# de déploiement et ne peut pas être devinée — un défaut silencieux sur
# localhost enverrait le lien de l'espace abonné mort dans chaque WhatsApp.
# `env(...)` sans `default` lève `ImproperlyConfigured` si la variable
# manque : le service refuse de démarrer plutôt que d'envoyer un lien mort.
FRONTEND_URL = env("FRONTEND_URL", default="http://localhost:4321") if DEBUG else env("FRONTEND_URL")

# --- Token d'accès abonné ---
DEFAULT_TOKEN_VALIDITE_JOURS = env.int("DEFAULT_TOKEN_VALIDITE_JOURS", default=20)

# --- Redis (notification de progression des diffusions à la gateway) ---
REDIS_URL = env("REDIS_URL", default="redis://localhost:6379/0")

# --- Limite de débit globale des envois WhatsApp (voir rate_limiter.py) ---
# Délai minimum (secondes) entre deux envois WhatsApp consécutifs, tous
# déclencheurs confondus (diffusion en lot ET envois individuels immédiats).
# Défaut aligné sur le rythme déjà choisi pour la diffusion en lot (5
# messages/15s ≈ 1 message/3s, voir schedulers.py) : cette limite ne fait
# donc que généraliser un rythme déjà jugé sûr à TOUS les envois, pas
# seulement à la diffusion en masse. Mettre à 0 désactive le throttling.
WHATSAPP_RATE_LIMIT_MIN_INTERVAL_SECONDS = env.float("WHATSAPP_RATE_LIMIT_MIN_INTERVAL_SECONDS", default=3.0)


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
        "filename": str(LOG_DIR / "notification.log"),
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
