"""Configuration Django du Campagne Service."""

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
    "campagnes",
]

MIDDLEWARE = [
    "django.middleware.security.SecurityMiddleware",
    "django.middleware.common.CommonMiddleware",
]

ROOT_URLCONF = "campagne.urls"

TEMPLATES = [
    {
        "BACKEND": "django.template.backends.django.DjangoTemplates",
        "DIRS": [],
        "APP_DIRS": True,
        "OPTIONS": {"context_processors": []},
    },
]

WSGI_APPLICATION = "campagne.wsgi.application"

# La CI fait tourner ces tests sur PostgreSQL 16 (même moteur qu'en prod) en
# positionnant FORCE_POSTGRES_TESTS=True (+ les CAMPAGNE_DB_* habituels
# pointés vers le service postgres du job). Par défaut (dev local), TESTING
# seul suffit à retomber sur SQLite en mémoire — rapide, zéro dépendance.
if TESTING and not env.bool("FORCE_POSTGRES_TESTS", default=False):
    DATABASES = {"default": {"ENGINE": "django.db.backends.sqlite3", "NAME": ":memory:"}}
else:
    DATABASES = {
        "default": {
            "ENGINE": "django.db.backends.postgresql",
            "HOST": env("CAMPAGNE_DB_HOST", default="localhost"),
            "PORT": env("CAMPAGNE_DB_PORT", default="5432"),
            "NAME": env("CAMPAGNE_DB_NAME", default="campagne_db"),
            "USER": env("CAMPAGNE_DB_USER", default="campagne_user"),
            "PASSWORD": env("CAMPAGNE_DB_PASSWORD", default=""),
        }
    }

# Isolation Postgres du trafic applicatif derrière un rôle `_runtime` non
# superutilisateur (voir `campagnes/db_hardening.py` — copie synchronisée
# depuis `libs/sgfe_common/`, AUDIT_SGFE.md §8·J). Sans effet sur SQLite
# (le receiver vérifie `connection.vendor` lui-même) : sûr à connecter
# inconditionnellement ici plutôt que sous le `else` ci-dessus.
from campagnes.db_hardening import connecter_isolement_runtime  # noqa: E402

connecter_isolement_runtime()

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
CAMPAGNE_GRPC_PORT = env.int("CAMPAGNE_GRPC_PORT", default=50053)

# --- Redis (pub/sub : notifie la gateway de l'avancement des campagnes) ---
REDIS_URL = env("REDIS_URL", default="redis://localhost:6379/0")

# Sentinel (voir redis/README.md à la racine) : résout le maître courant au
# lieu de l'hôte fixe ci-dessus, qui devient une réplique en lecture seule
# après une bascule. Vide par défaut (repli sur REDIS_URL) : Sentinel n'est
# pas forcément démarré en développement local hors Docker Compose.
REDIS_SENTINELS = env("REDIS_SENTINELS", default="")
REDIS_SENTINEL_MASTER = env("REDIS_SENTINEL_MASTER", default="mymaster")

# --- Services gRPC consommés ---
ABONNE_GRPC_HOST = env("ABONNE_GRPC_HOST", default="localhost")
ABONNE_GRPC_PORT = env.int("ABONNE_GRPC_PORT", default=50052)

FACTURATION_GRPC_HOST = env("FACTURATION_GRPC_HOST", default="localhost")
FACTURATION_GRPC_PORT = env.int("FACTURATION_GRPC_PORT", default=50054)

NOTIFICATION_GRPC_HOST = env("NOTIFICATION_GRPC_HOST", default="localhost")
NOTIFICATION_GRPC_PORT = env.int("NOTIFICATION_GRPC_PORT", default=50056)

# Reporting Service — pour pousser les stats de campagne à la clôture (ADR-019).
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
    # Phase 2 de l'observabilité : ce flux (stdout du conteneur) est celui
    # qu'Alloy scrape et pousse vers Loki — voir OTEL_LOGS_EXPORTER=none dans
    # docker-compose.yml, décision déjà actée : les logs partent par stdout
    # JSON, pas par l'exportateur OTLP natif. D'où le format JSON ici plutôt
    # que le texte lisible "iso8601" d'avant — c'est ce format-là qu'un humain
    # lira via Grafana/Loki une fois la plateforme branchée, pas
    # `docker compose logs` en brut. Le handler "file" ci-dessous (hash
    # chaîné, preuve d'intégrité SOC2) n'est PAS ce flux et reste inchangé —
    # les deux coexistent, l'un n'est pas une copie de l'autre.
    "console": {
        "class": "logging.StreamHandler",
        "formatter": "json",
        "filters": ["trace_context"],
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
        "filename": str(LOG_DIR / "campagne.log"),
        "when": "midnight",
        "utc": True,
        "backupCount": LOG_RETENTION_DAYS,
        # Chaînage de hash tamper-evident (voir campagnes/log_integrity.py,
        # AUDIT_SGFE.md §J "Journalisation de sécurité centralisée et
        # inviolable") — UNIQUEMENT sur ce handler fichier, jamais "console"
        # (voir la docstring de ChainedHashFormatter pour la raison).
        "formatter": "iso8601_chained",
    }

LOGGING: dict[str, object] = {
    "version": 1,
    "disable_existing_loggers": False,
    "filters": {
        "trace_context": {"()": "campagnes.logging_utils.TraceContextFilter"},
    },
    "formatters": {
        "iso8601_chained": {
            "()": "campagnes.log_integrity.ChainedHashFormatter",
            "format": "%(asctime)s.%(msecs)03dZ %(levelname)s %(name)s %(message)s",
            "datefmt": "%Y-%m-%dT%H:%M:%S",
        },
        # trace_id/span_id : voir campagnes/logging_utils.py::TraceContextFilter.
        "json": {
            "()": "pythonjsonlogger.jsonlogger.JsonFormatter",
            "format": "%(asctime)s %(levelname)s %(name)s %(message)s %(trace_id)s %(span_id)s",
            "datefmt": "%Y-%m-%dT%H:%M:%S",
        },
    },
    "handlers": _LOGGING_HANDLER_CONFIG,
    "root": {
        "handlers": _LOGGING_HANDLERS,
        "level": env("DJANGO_LOG_LEVEL", default="INFO"),
    },
}


# Profiling continu (Pyroscope, phase suivante du plan d'observabilité — voir
# CLAUDE.md §Observabilité). Contrairement aux secrets exigés via `${VAR:?...}`
# dans docker-compose.yml (DJANGO_SECRET_KEY, INTERNAL_GRPC_KEY, etc. —
# fail-fast), PYROSCOPE_SERVER_ADDRESS reste optionnelle : dégradation
# gracieuse, jamais un motif d'échec au démarrage du service. Le profiling est
# un bonus d'observabilité, pas un contrôle de sécurité.
if env("PYROSCOPE_SERVER_ADDRESS", default=""):
    import pyroscope  # type: ignore[import-untyped]  # pas de stubs publiés pour pyroscope-io

    pyroscope.configure(
        application_name="campagne-service",
        server_address=env("PYROSCOPE_SERVER_ADDRESS", default=""),
        tags={"service": "campagne-service"},
    )
