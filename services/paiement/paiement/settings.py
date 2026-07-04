"""Configuration Django du Paiement Service."""

import sys
from pathlib import Path

import environ

BASE_DIR = Path(__file__).resolve().parent.parent

env = environ.Env()
environ.Env.read_env(BASE_DIR / ".env")

TESTING = "test" in sys.argv or env.bool("USE_SQLITE", default=False)

SECRET_KEY = env("DJANGO_SECRET_KEY", default="django-insecure-dev-key-change-me")
DEBUG = env.bool("DJANGO_DEBUG", default=True)
ALLOWED_HOSTS = env.list("DJANGO_ALLOWED_HOSTS", default=["localhost", "127.0.0.1"])

INSTALLED_APPS = [
    "django.contrib.contenttypes",
    "django.contrib.staticfiles",
    "paiements",
]

MIDDLEWARE = [
    "django.middleware.security.SecurityMiddleware",
    "django.middleware.common.CommonMiddleware",
]

ROOT_URLCONF = "paiement.urls"

TEMPLATES = [
    {
        "BACKEND": "django.template.backends.django.DjangoTemplates",
        "DIRS": [],
        "APP_DIRS": True,
        "OPTIONS": {"context_processors": []},
    },
]

WSGI_APPLICATION = "paiement.wsgi.application"

if TESTING:
    DATABASES = {"default": {"ENGINE": "django.db.backends.sqlite3", "NAME": BASE_DIR / "dev.sqlite3"}}
else:
    DATABASES = {
        "default": {
            "ENGINE": "django.db.backends.postgresql",
            "HOST": env("PAIEMENT_DB_HOST", default="localhost"),
            "PORT": env("PAIEMENT_DB_PORT", default="5432"),
            "NAME": env("PAIEMENT_DB_NAME", default="paiement_db"),
            "USER": env("PAIEMENT_DB_USER", default="paiement_user"),
            "PASSWORD": env("PAIEMENT_DB_PASSWORD", default=""),
        }
    }

LANGUAGE_CODE = "en-us"
TIME_ZONE = "UTC"
USE_I18N = True
USE_TZ = True

STATIC_URL = "static/"
DEFAULT_AUTO_FIELD = "django.db.models.BigAutoField"

# --- gRPC ---
PAIEMENT_GRPC_PORT = env.int("PAIEMENT_GRPC_PORT", default=50055)

# --- Redis (pub/sub : notifie la gateway des paiements enregistrés) ---
REDIS_URL = env("REDIS_URL", default="redis://localhost:6379/0")

# --- Services gRPC consommés ---
FACTURATION_GRPC_HOST = env("FACTURATION_GRPC_HOST", default="localhost")
FACTURATION_GRPC_PORT = env.int("FACTURATION_GRPC_PORT", default=50054)

NOTIFICATION_GRPC_HOST = env("NOTIFICATION_GRPC_HOST", default="localhost")
NOTIFICATION_GRPC_PORT = env.int("NOTIFICATION_GRPC_PORT", default=50056)

ABONNE_GRPC_HOST = env("ABONNE_GRPC_HOST", default="localhost")
ABONNE_GRPC_PORT = env.int("ABONNE_GRPC_PORT", default=50052)

CONFIG_GRPC_HOST = env("CONFIG_GRPC_HOST", default="localhost")
CONFIG_GRPC_PORT = env.int("CONFIG_GRPC_PORT", default=50058)

# --- JWT (validation interne) ---
JWT_SECRET_KEY = env("JWT_SECRET_KEY", default="changeme")
JWT_ALGORITHM = env("JWT_ALGORITHM", default="HS256")

# --- Délais impayés (défauts, surchargés par Config Service) ---
DEFAULT_DELAI_RAPPEL_1 = 0  # Jours après date limite pour 1er rappel
DEFAULT_DELAI_RAPPEL_2 = 3  # Jours après date limite pour 2ème rappel
DEFAULT_DELAI_AVERTISSEMENT = 7  # Jours après date limite pour avertissement
DEFAULT_DELAI_SUSPENSION = 10  # Jours après date limite pour suspension
DEFAULT_SUSPENSION_AUTO = True  # Activer la suspension automatique
DEFAULT_SUSPENSION_RELANCES = 5  # Jours de suspension des relances après paiement partiel
