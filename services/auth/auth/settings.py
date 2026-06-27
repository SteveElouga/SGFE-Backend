"""Configuration Django du Auth Service."""

import sys
from datetime import timedelta
from pathlib import Path

import environ

BASE_DIR = Path(__file__).resolve().parent.parent
TESTING = "test" in sys.argv

env = environ.Env()
environ.Env.read_env(BASE_DIR / ".env")

SECRET_KEY = env("DJANGO_SECRET_KEY", default="django-insecure-dev-key-change-me")
DEBUG = env.bool("DJANGO_DEBUG", default=True)
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

# --- JWT ---
JWT_SECRET_KEY = env("JWT_SECRET_KEY", default=SECRET_KEY)
JWT_ALGORITHM = env("JWT_ALGORITHM", default="HS256")
JWT_ACCESS_TOKEN_LIFETIME = timedelta(hours=env.int("JWT_ACCESS_TOKEN_EXPIRE_HOURS", default=24))
JWT_REFRESH_TOKEN_LIFETIME = timedelta(days=env.int("JWT_REFRESH_TOKEN_EXPIRE_DAYS", default=7))

# --- Sécurité ---
MAX_LOGIN_ATTEMPTS = env.int("MAX_LOGIN_ATTEMPTS", default=5)
LOCKOUT_DURATION_MINUTES = env.int("LOCKOUT_DURATION_MINUTES", default=15)
BCRYPT_ROUNDS = env.int("BCRYPT_ROUNDS", default=12)

SIMPLE_JWT = {
    "ACCESS_TOKEN_LIFETIME": JWT_ACCESS_TOKEN_LIFETIME,
    "REFRESH_TOKEN_LIFETIME": JWT_REFRESH_TOKEN_LIFETIME,
    "ALGORITHM": JWT_ALGORITHM,
    "SIGNING_KEY": JWT_SECRET_KEY,
    "USER_ID_FIELD": "id",
    "USER_ID_CLAIM": "user_id",
}
