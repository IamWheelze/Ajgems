"""Django settings for the minefoot web app (FRD section 11: modular monolith, PostGIS, Celery).

Configuration comes from environment variables so the same code runs on a laptop, in CI and on a
server. Development defaults match docker-compose.yml.
"""
import os
from pathlib import Path

BASE_DIR = Path(__file__).resolve().parent.parent
REPO_ROOT = BASE_DIR.parent.parent

SECRET_KEY = os.environ.get("DJANGO_SECRET_KEY", "dev-only-not-secret")
DEBUG = os.environ.get("DJANGO_DEBUG", "1") == "1"
if not DEBUG and SECRET_KEY == "dev-only-not-secret":
    raise RuntimeError("set DJANGO_SECRET_KEY when DJANGO_DEBUG is off")
ALLOWED_HOSTS = os.environ.get("DJANGO_ALLOWED_HOSTS", "localhost,127.0.0.1").split(",")
CSRF_TRUSTED_ORIGINS = os.environ.get("DJANGO_CSRF_TRUSTED_ORIGINS", "http://localhost:5173").split(",")

INSTALLED_APPS = [
    "django.contrib.admin",
    "django.contrib.auth",
    "django.contrib.contenttypes",
    "django.contrib.sessions",
    "django.contrib.messages",
    "django.contrib.staticfiles",
    "django.contrib.gis",
    "rest_framework",
    "accounts",
    "projects",
    "review",
    "audit",
    "registry",
    "datasets",
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

ROOT_URLCONF = "minefoot_web.urls"
TEMPLATES = [{
    "BACKEND": "django.template.backends.django.DjangoTemplates", "DIRS": [], "APP_DIRS": True,
    "OPTIONS": {"context_processors": ["django.template.context_processors.request",
                                       "django.contrib.auth.context_processors.auth",
                                       "django.contrib.messages.context_processors.messages"]},
}]
WSGI_APPLICATION = "minefoot_web.wsgi.application"

DATABASES = {
    "default": {
        "ENGINE": "django.contrib.gis.db.backends.postgis",
        "NAME": os.environ.get("POSTGRES_DB", "minefoot"),
        "USER": os.environ.get("POSTGRES_USER", "minefoot"),
        "PASSWORD": os.environ.get("POSTGRES_PASSWORD", "minefoot"),
        "HOST": os.environ.get("POSTGRES_HOST", "localhost"),
        "PORT": os.environ.get("POSTGRES_PORT", "5432"),
    }
}

AUTH_USER_MODEL = "accounts.User"
AUTH_PASSWORD_VALIDATORS = [
    {"NAME": "django.contrib.auth.password_validation.UserAttributeSimilarityValidator"},
    {"NAME": "django.contrib.auth.password_validation.MinimumLengthValidator", "OPTIONS": {"min_length": 10}},
    {"NAME": "django.contrib.auth.password_validation.CommonPasswordValidator"},
]

LANGUAGE_CODE = "en-gb"
TIME_ZONE = "UTC"
USE_I18N = True
USE_TZ = True
STATIC_URL = "static/"
DEFAULT_AUTO_FIELD = "django.db.models.BigAutoField"

REST_FRAMEWORK = {
    "DEFAULT_AUTHENTICATION_CLASSES": ["rest_framework.authentication.SessionAuthentication"],
    "DEFAULT_PERMISSION_CLASSES": ["rest_framework.permissions.IsAuthenticated"],
    "DEFAULT_PAGINATION_CLASS": "rest_framework.pagination.LimitOffsetPagination",
    "PAGE_SIZE": 200,
}

# Product limits (FRD). Provisional values are flagged where the FRD says so.
MINEFOOT = {
    "AOI_MAX_KM2": float(os.environ.get("MINEFOOT_AOI_MAX_KM2", "10000")),          # FR-013
    "DATA_ROOT": Path(os.environ.get("MINEFOOT_DATA_ROOT", REPO_ROOT / "data")),
    "IMAGE_CACHE": Path(os.environ.get("MINEFOOT_IMAGE_CACHE", REPO_ROOT / "data" / "web_cache")),
    # Detection jobs (W2). The model is a finished minefoot G2 run until the registry exists (FR-102).
    "MODEL_RUN": Path(os.environ.get("MINEFOOT_MODEL_RUN", REPO_ROOT / "data" / "runs" / "g2_hn")),
    "JOB_ROOT": Path(os.environ.get("MINEFOOT_JOB_ROOT", REPO_ROOT / "data" / "jobs")),
    "TILE_KM": float(os.environ.get("MINEFOOT_TILE_KM", "10")),
    "MMU_HA": float(os.environ.get("MINEFOOT_MMU_HA", "1.0")),
    # Measured 2026-11-09: 13 s per 10 km tile on 4 CPU cores (fetch, prepare, score, vectorise). 20 s is cautious.
    "SECONDS_PER_TILE": float(os.environ.get("MINEFOOT_SECONDS_PER_TILE", "20")),
    # FR-086 money rates; 0 means not priced (own CPU, free Sentinel-2 imagery). Set them to show money costs.
    "CPU_COST_PER_HOUR": float(os.environ.get("MINEFOOT_CPU_COST_PER_HOUR", "0")),
    "STORAGE_COST_PER_GB_MONTH": float(os.environ.get("MINEFOOT_STORAGE_COST_PER_GB_MONTH", "0")),
    "COST_CURRENCY": os.environ.get("MINEFOOT_COST_CURRENCY", "USD"),
    # FR-053 coverage thresholds (provisional): warn below 80% usable pixels, block below 40%.
    "COVERAGE_WARN_BELOW": float(os.environ.get("MINEFOOT_COVERAGE_WARN_BELOW", "0.80")),
    "COVERAGE_BLOCK_BELOW": float(os.environ.get("MINEFOOT_COVERAGE_BLOCK_BELOW", "0.40")),
    "NIGERIA_AOIS": Path(os.environ.get("MINEFOOT_NIGERIA_AOIS", REPO_ROOT / "configs" / "nigeria_aois.yaml")),
    "NIGERIA_TEST_LOCK": Path(os.environ.get("MINEFOOT_NIGERIA_TEST_LOCK",
                                             REPO_ROOT / "docs" / "evidence" / "nigeria_test_lock_v1.json")),
    "NIGERIA_G3_RESULT": Path(os.environ.get("MINEFOOT_NIGERIA_G3_RESULT",
                                             REPO_ROOT / "docs" / "evidence" / "g3_v1" / "g3_metrics.json")),
}

CELERY_BROKER_URL = os.environ.get("CELERY_BROKER_URL", "redis://localhost:6379/0")
CELERY_TASK_ALWAYS_EAGER = os.environ.get("CELERY_TASK_ALWAYS_EAGER", "0") == "1"

LOGGING = {"version": 1, "handlers": {"console": {"class": "logging.StreamHandler"}},
           "root": {"handlers": ["console"], "level": "INFO"}}
