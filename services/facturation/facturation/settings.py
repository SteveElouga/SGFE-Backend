"""Configuration Django du Facturation Service."""

import sys
from pathlib import Path

import environ

BASE_DIR = Path(__file__).resolve().parent.parent

env = environ.Env()
environ.Env.read_env(BASE_DIR / ".env")

TESTING = "test" in sys.argv or env.bool("USE_SQLITE", default=False)

SECRET_KEY = env("DJANGO_SECRET_KEY")
DEBUG = env.bool("DJANGO_DEBUG", default=False)
ALLOWED_HOSTS = env.list("DJANGO_ALLOWED_HOSTS", default=["localhost", "127.0.0.1"])

INSTALLED_APPS = [
    "django.contrib.contenttypes",
    "django.contrib.staticfiles",
    "factures",
]

MIDDLEWARE = [
    "django.middleware.security.SecurityMiddleware",
    "django.middleware.common.CommonMiddleware",
]

ROOT_URLCONF = "facturation.urls"

TEMPLATES = [
    {
        "BACKEND": "django.template.backends.django.DjangoTemplates",
        "DIRS": [],
        "APP_DIRS": True,
        "OPTIONS": {"context_processors": []},
    },
]

WSGI_APPLICATION = "facturation.wsgi.application"

if TESTING:
    DATABASES = {"default": {"ENGINE": "django.db.backends.sqlite3", "NAME": BASE_DIR / "dev.sqlite3"}}
else:
    DATABASES = {
        "default": {
            "ENGINE": "django.db.backends.postgresql",
            "HOST": env("FACTURATION_DB_HOST", default="localhost"),
            "PORT": env("FACTURATION_DB_PORT", default="5432"),
            "NAME": env("FACTURATION_DB_NAME", default="facturation_db"),
            "USER": env("FACTURATION_DB_USER", default="facturation_user"),
            "PASSWORD": env("FACTURATION_DB_PASSWORD", default=""),
        }
    }

LANGUAGE_CODE = "fr-fr"
TIME_ZONE = "UTC"
USE_I18N = True
USE_TZ = True

STATIC_URL = "static/"
DEFAULT_AUTO_FIELD = "django.db.models.BigAutoField"

# --- gRPC ---
FACTURATION_GRPC_PORT = env.int("FACTURATION_GRPC_PORT", default=50054)

# --- Services gRPC consommés ---
CAMPAGNE_GRPC_HOST = env("CAMPAGNE_GRPC_HOST", default="localhost")
CAMPAGNE_GRPC_PORT = env.int("CAMPAGNE_GRPC_PORT", default=50053)

CONFIG_GRPC_HOST = env("CONFIG_GRPC_HOST", default="localhost")
CONFIG_GRPC_PORT = env.int("CONFIG_GRPC_PORT", default=50058)

PAIEMENT_GRPC_HOST = env("PAIEMENT_GRPC_HOST", default="localhost")
PAIEMENT_GRPC_PORT = env.int("PAIEMENT_GRPC_PORT", default=50055)

NOTIFICATION_GRPC_HOST = env("NOTIFICATION_GRPC_HOST", default="localhost")
NOTIFICATION_GRPC_PORT = env.int("NOTIFICATION_GRPC_PORT", default=50056)

# Abonné Service — lecture de l'identité de l'abonné (nom, adresse, WhatsApp,
# n° compteur) pour l'affichage sur le PDF de facture.
ABONNE_GRPC_HOST = env("ABONNE_GRPC_HOST", default="localhost")
ABONNE_GRPC_PORT = env.int("ABONNE_GRPC_PORT", default=50052)

# Reporting Service — pour pousser les stats de facturation (ADR-019, read model).
REPORTING_GRPC_HOST = env("REPORTING_GRPC_HOST", default="localhost")
REPORTING_GRPC_PORT = env.int("REPORTING_GRPC_PORT", default=50057)

# Authentification de la couche gRPC interne (registre, point 1).
#
# Secret partagé entre tous les services. Sans lui, le serveur gRPC refuse de
# démarrer — même en développement : une valeur par défaut silencieuse
# recréerait exactement le trou qu'on ferme, un contrôle qui a l'air posé et
# ne protège rien.
INTERNAL_GRPC_KEY = env("INTERNAL_GRPC_KEY", default="")


# --- JWT (validation interne) ---
JWT_ALGORITHM = env("JWT_ALGORITHM", default="HS256")

# --- PDF ---
PDF_STORAGE_DIR = env("PDF_STORAGE_DIR", default=str(BASE_DIR / "pdfs"))

# --- Redis (pub/sub : notifie la gateway des mutations de facture) ---
REDIS_URL = env("REDIS_URL", default="redis://localhost:6379/0")

# --- Délai de paiement par défaut (en jours) si Config Service est indisponible ---
DEFAULT_DELAI_PAIEMENT_JOURS = 5
