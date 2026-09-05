"""Configuration Django du Reporting Service (agrégateur read-only, ADR-019)."""

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
    "django.contrib.contenttypes",
    "django.contrib.staticfiles",
    "stats",
]

MIDDLEWARE = [
    "django.middleware.security.SecurityMiddleware",
    "django.middleware.common.CommonMiddleware",
]

ROOT_URLCONF = "reporting.urls"

TEMPLATES = [
    {
        "BACKEND": "django.template.backends.django.DjangoTemplates",
        "DIRS": [],
        "APP_DIRS": True,
        "OPTIONS": {"context_processors": []},
    },
]

WSGI_APPLICATION = "reporting.wsgi.application"

# La CI fait tourner ces tests sur PostgreSQL 16 (même moteur qu'en prod) en
# positionnant FORCE_POSTGRES_TESTS=True (+ les REPORTING_DB_* habituels
# pointés vers le service postgres du job). Par défaut (dev local), TESTING
# seul suffit à retomber sur SQLite en mémoire — rapide, zéro dépendance.
if TESTING and not env.bool("FORCE_POSTGRES_TESTS", default=False):
    DATABASES = {"default": {"ENGINE": "django.db.backends.sqlite3", "NAME": ":memory:"}}
else:
    DATABASES = {
        "default": {
            "ENGINE": "django.db.backends.postgresql",
            "HOST": env("REPORTING_DB_HOST", default="localhost"),
            "PORT": env("REPORTING_DB_PORT", default="5432"),
            "NAME": env("REPORTING_DB_NAME", default="reporting_db"),
            "USER": env("REPORTING_DB_USER", default="reporting_user"),
            "PASSWORD": env("REPORTING_DB_PASSWORD", default=""),
        }
    }

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
REPORTING_GRPC_PORT = env.int("REPORTING_GRPC_PORT", default=50057)

# Services externes consommés par le job de réconciliation nocturne
# (stats/schedulers.py) : Facturation et Paiement sont les sources de vérité
# depuis lesquelles StatsFacturation/StatsPaiements sont recalculées.
FACTURATION_GRPC_HOST = env("FACTURATION_GRPC_HOST", default="localhost")
FACTURATION_GRPC_PORT = env.int("FACTURATION_GRPC_PORT", default=50054)
PAIEMENT_GRPC_HOST = env("PAIEMENT_GRPC_HOST", default="localhost")
PAIEMENT_GRPC_PORT = env.int("PAIEMENT_GRPC_PORT", default=50055)

# Authentification de la couche gRPC interne (registre, point 1).
#
# Secret partagé entre tous les services. Sans lui, le serveur gRPC refuse de
# démarrer — même en développement : une valeur par défaut silencieuse
# recréerait exactement le trou qu'on ferme, un contrôle qui a l'air posé et
# ne protège rien.
INTERNAL_GRPC_KEY = env("INTERNAL_GRPC_KEY", default="")


# --- Redis (flux d'événements consommé par le read model) ---
REDIS_URL = env("REDIS_URL", default="redis://localhost:6379/0")


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
        "filename": str(LOG_DIR / "reporting.log"),
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
