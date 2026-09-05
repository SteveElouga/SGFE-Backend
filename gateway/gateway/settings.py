"""Configuration Django de l'API Gateway — pas de base de données."""

import logging
import sys
import time
from pathlib import Path

import environ

BASE_DIR = Path(__file__).resolve().parent.parent
# Comme les 8 microservices (voir leur settings.py) : évite d'écrire des
# fichiers de log sur disque à chaque `manage.py test`.
TESTING = "test" in sys.argv

env = environ.Env()
environ.Env.read_env(BASE_DIR / ".env")

SECRET_KEY = env("DJANGO_SECRET_KEY")
DEBUG = env.bool("DJANGO_DEBUG", default=False)
ALLOWED_HOSTS = env.list("DJANGO_ALLOWED_HOSTS", default=["localhost", "127.0.0.1"])

INSTALLED_APPS = [
    "django.contrib.staticfiles",
    "schema",
]

MIDDLEWARE = [
    # En tête de chaîne, avant toute authentification et tout resolver
    # GraphQL — voir `schema.identity_context.reset_identity` pour le
    # rationale (isolation de l'identité entre deux requêtes qui
    # réutiliseraient le même thread/contexte).
    "schema.identity_context.ResetIdentityMiddleware",
    "django.middleware.security.SecurityMiddleware",
    "django.middleware.common.CommonMiddleware",
    "django.middleware.clickjacking.XFrameOptionsMiddleware",
]

# --- En-têtes de sécurité ---
# Django pose nosniff / Referrer-Policy / X-Frame-Options / HSTS ; le CSP est
# posé au niveau nginx (edge), Django ne le gérant pas nativement.
SECURE_CONTENT_TYPE_NOSNIFF = True  # X-Content-Type-Options: nosniff
SECURE_REFERRER_POLICY = "strict-origin-when-cross-origin"
X_FRAME_OPTIONS = "DENY"
# Derrière nginx (qui pose X-Forwarded-Proto), Django reconnaît le HTTPS d'origine.
SECURE_PROXY_SSL_HEADER = ("HTTP_X_FORWARDED_PROTO", "https")
# HSTS uniquement en prod (HTTPS) — en dev on laisse inactif pour ne pas
# verrouiller le navigateur sur https://localhost.
if not DEBUG:
    SECURE_HSTS_SECONDS = 31_536_000  # 1 an
    SECURE_HSTS_INCLUDE_SUBDOMAINS = True
    SECURE_HSTS_PRELOAD = True

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

REPORTING_GRPC_HOST = env("REPORTING_GRPC_HOST", default="localhost")
REPORTING_GRPC_PORT = env.int("REPORTING_GRPC_PORT", default=50057)

# Authentification de la couche gRPC interne (registre, point 1).
#
# Secret partagé entre tous les services. Sans lui, le serveur gRPC refuse de
# démarrer — même en développement : une valeur par défaut silencieuse
# recréerait exactement le trou qu'on ferme, un contrôle qui a l'air posé et
# ne protège rien.
INTERNAL_GRPC_KEY = env("INTERNAL_GRPC_KEY", default="")


REDIS_URL = env("REDIS_URL", default="redis://localhost:6379/0")

# Le refresh token n'est jamais renvoyé dans le corps de la réponse GraphQL :
# il est posé en cookie HttpOnly par login/refreshToken, inaccessible à JS
# (protection XSS), et lu depuis ce cookie par refreshToken/logout.
REFRESH_TOKEN_COOKIE_NAME = "refresh_token"
REFRESH_TOKEN_COOKIE_MAX_AGE = env.int("JWT_REFRESH_TOKEN_EXPIRE_DAYS", default=7) * 86400
# Littéral, jamais dérivé de DEBUG (registre CONFORMITE_SOC2_OWASP.md, item 11) :
# un cookie de refresh sans l'attribut Secure resterait exploitable en clair sur
# une origine http:// mal configurée. Le dev local passe déjà par le nginx TLS
# auto-signé (voir CLAUDE.md racine, §Frontend) donc ça ne casse rien en local.
REFRESH_TOKEN_COOKIE_SECURE = True

# --- Journalisation (voir AUDIT_SGFE.md §J : rétention + horodatage fiable) ---
#
# Horodatage UTC explicite : `logging.Formatter.converter` est basculé sur
# `time.gmtime` pour tout le processus (cohérent avec `TIME_ZONE = "UTC"`
# déjà en vigueur) — des journaux de plusieurs conteneurs qui ne s'accordent
# pas sur l'heure ne sont pas exploitables comme preuve. Rétention
# configurable via `LOG_RETENTION_DAYS` (défaut 30 jours) :
# `TimedRotatingFileHandler` tourne un fichier par jour et purge au-delà.
# Porte aussi le logger dédié `security` (voir `schema/context.py`).
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
# `manage.py test`, comme les 8 microservices.
if not TESTING:
    LOG_DIR.mkdir(parents=True, exist_ok=True)
    _LOGGING_HANDLERS.append("file")
    _LOGGING_HANDLER_CONFIG["file"] = {
        "class": "logging.handlers.TimedRotatingFileHandler",
        "filename": str(LOG_DIR / "gateway.log"),
        "when": "midnight",
        "utc": True,
        "backupCount": LOG_RETENTION_DAYS,
        # Chaînage de hash tamper-evident (voir schema/log_integrity.py,
        # AUDIT_SGFE.md §J "Journalisation de sécurité centralisée et
        # inviolable") — UNIQUEMENT sur ce handler fichier, jamais "console"
        # (voir la docstring de ChainedHashFormatter pour la raison). Couvre
        # notamment le logger `security` (ci-dessus), qui écrit sur "file"
        # comme tous les autres loggers de ce composant.
        "formatter": "iso8601_chained",
    }

LOGGING: dict[str, object] = {
    "version": 1,
    "disable_existing_loggers": False,
    "formatters": {
        "iso8601": {
            "format": "%(asctime)s.%(msecs)03dZ %(levelname)s %(name)s %(message)s",
            "datefmt": "%Y-%m-%dT%H:%M:%S",
        },
        "iso8601_chained": {
            "()": "schema.log_integrity.ChainedHashFormatter",
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
