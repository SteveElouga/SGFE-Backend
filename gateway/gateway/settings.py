"""Configuration Django de l'API Gateway — pas de base de données."""

from pathlib import Path

import environ

BASE_DIR = Path(__file__).resolve().parent.parent

env = environ.Env()
environ.Env.read_env(BASE_DIR / ".env")

SECRET_KEY = env("DJANGO_SECRET_KEY", default="django-insecure-dev-key-change-me")
DEBUG = env.bool("DJANGO_DEBUG", default=True)
ALLOWED_HOSTS = env.list("DJANGO_ALLOWED_HOSTS", default=["localhost", "127.0.0.1"])

INSTALLED_APPS = [
    "django.contrib.staticfiles",
    "schema",
]

MIDDLEWARE = [
    "django.middleware.common.CommonMiddleware",
]

ROOT_URLCONF = "gateway.urls"

TEMPLATES = [
    {
        "BACKEND": "django.template.backends.django.DjangoTemplates",
        "DIRS": [],
        "APP_DIRS": True,
        "OPTIONS": {"context_processors": []},
    },
]

ASGI_APPLICATION = "gateway.asgi.application"

# Pas de base de données : la gateway ne fait que router vers les services gRPC.
DATABASES = {"default": {"ENGINE": "django.db.backends.dummy"}}

LANGUAGE_CODE = "en-us"
TIME_ZONE = "UTC"
USE_I18N = True
USE_TZ = True

STATIC_URL = "static/"
DEFAULT_AUTO_FIELD = "django.db.models.BigAutoField"

GATEWAY_HTTP_PORT = env.int("GATEWAY_HTTP_PORT", default=8000)

GRPC_TIMEOUT = env.int("GRPC_TIMEOUT", default=30)

AUTH_GRPC_HOST = env("AUTH_GRPC_HOST", default="localhost")
AUTH_GRPC_PORT = env.int("AUTH_GRPC_PORT", default=50051)

ABONNE_GRPC_HOST = env("ABONNE_GRPC_HOST", default="localhost")
ABONNE_GRPC_PORT = env.int("ABONNE_GRPC_PORT", default=50052)

CAMPAGNE_GRPC_HOST = env("CAMPAGNE_GRPC_HOST", default="localhost")
CAMPAGNE_GRPC_PORT = env.int("CAMPAGNE_GRPC_PORT", default=50053)

FACTURATION_GRPC_HOST = env("FACTURATION_GRPC_HOST", default="localhost")
FACTURATION_GRPC_PORT = env.int("FACTURATION_GRPC_PORT", default=50054)

PAIEMENT_GRPC_HOST = env("PAIEMENT_GRPC_HOST", default="localhost")
PAIEMENT_GRPC_PORT = env.int("PAIEMENT_GRPC_PORT", default=50055)

NOTIFICATION_GRPC_HOST = env("NOTIFICATION_GRPC_HOST", default="localhost")
NOTIFICATION_GRPC_PORT = env.int("NOTIFICATION_GRPC_PORT", default=50056)

CONFIG_GRPC_HOST = env("CONFIG_GRPC_HOST", default="localhost")
CONFIG_GRPC_PORT = env.int("CONFIG_GRPC_PORT", default=50058)

REDIS_URL = env("REDIS_URL", default="redis://localhost:6379/0")

# Le refresh token n'est jamais renvoyé dans le corps de la réponse GraphQL :
# il est posé en cookie HttpOnly par login/refreshToken, inaccessible à JS
# (protection XSS), et lu depuis ce cookie par refreshToken/logout.
REFRESH_TOKEN_COOKIE_NAME = "refresh_token"
REFRESH_TOKEN_COOKIE_MAX_AGE = env.int("JWT_REFRESH_TOKEN_EXPIRE_DAYS", default=7) * 86400
REFRESH_TOKEN_COOKIE_SECURE = env.bool("COOKIE_SECURE", default=not DEBUG)
