import os
import re
import tempfile
from pathlib import Path

from . import modules

BASE_DIR = Path(__file__).resolve().parent.parent


def _load_env(path):
    if not path.exists():
        return
    for line in path.read_text().splitlines():
        line = line.strip()
        if line and not line.startswith("#") and "=" in line:
            key, value = line.split("=", 1)
            os.environ.setdefault(key.strip(), value.strip().strip('"'))


_load_env(BASE_DIR / ".env")


def env_bool(name, default=False):
    return os.environ.get(name, str(default)).lower() in ("1", "true", "yes", "on")


DEBUG = env_bool("DEBUG", False)
SECRET_KEY = os.environ.get("SECRET_KEY", "")
if not SECRET_KEY:
    if DEBUG or os.environ.get("TESTING"):
        SECRET_KEY = "dev-only-not-for-production"
    else:
        raise RuntimeError("SECRET_KEY must be set in production.")

# COOLIFY_URL holds the application's public address, set by Coolify itself.
_public_urls = [u for u in os.environ.get("COOLIFY_URL", "").split(",") if u]
ALLOWED_HOSTS = [h for h in os.environ.get("ALLOWED_HOSTS", "localhost").split(",") if h]
ALLOWED_HOSTS += ["127.0.0.1"] + [u.split("://")[-1].split("/")[0].split(":")[0] for u in _public_urls]
CSRF_TRUSTED_ORIGINS = [o for o in os.environ.get("CSRF_TRUSTED_ORIGINS", "").split(",") if o] + _public_urls

INSTALLED_APPS = [
    "django.contrib.auth",
    "django.contrib.contenttypes",
    "django.contrib.sessions",
    "django.contrib.messages",
    "django.contrib.staticfiles",
    *modules.apps(),  # the platform and its modules, see schule/modules.py
]

MIDDLEWARE = [
    "django.middleware.security.SecurityMiddleware",
    "core.middleware.ModuleStaticMiddleware",  # WhiteNoise, with the static files of module bundles
    "core.middleware.ActiveModulesMiddleware",
    "core.middleware.PageCompressionMiddleware",
    "django.contrib.sessions.middleware.SessionMiddleware",
    "django.middleware.common.CommonMiddleware",
    "django.middleware.csrf.CsrfViewMiddleware",
    "django.contrib.auth.middleware.AuthenticationMiddleware",
    "core.middleware.BodyLimitMiddleware",
    "django.contrib.messages.middleware.MessageMiddleware",
    "django.middleware.clickjacking.XFrameOptionsMiddleware",
    "core.middleware.CurrentUserMiddleware",
    "core.middleware.UserLanguageMiddleware",
    "core.middleware.LoginRequiredMiddleware",
    "core.middleware.TwoFactorMiddleware",
    "sync.middleware.ConnectedMacMiddleware",
    "core.middleware.SecurityHeadersMiddleware",
    "core.middleware.ErrorLogMiddleware",
]

ROOT_URLCONF = "schule.urls"
WSGI_APPLICATION = "schule.wsgi.application"

TEMPLATES = [
    {
        "BACKEND": "django.template.backends.django.DjangoTemplates",
        "DIRS": [BASE_DIR / "templates"],
        "APP_DIRS": True,
        "OPTIONS": {
            "context_processors": [
                "django.template.context_processors.request",
                "django.contrib.auth.context_processors.auth",
                "django.contrib.messages.context_processors.messages",
                "core.context.app_context",
                "core.views_language.language_context",
            ],
        },
    },
]


def _database():
    url = os.environ.get("DATABASE_URL", "postgres://schule@127.0.0.1:5544/schule")
    if url.startswith(("sqlite", "sqlcipher")):
        # The desktop client. A second writer waits for the first instead of failing with "database is locked".
        cipher = url.startswith("sqlcipher")
        return {
            "ENGINE": "schule.sqlcipher" if cipher else "django.db.backends.sqlite3",
            "KEY": os.environ.get("SQLCIPHER_KEY", "") if cipher else "",
            "NAME": os.environ.get("SQLITE_PATH", BASE_DIR / "db.sqlite3"),
            "OPTIONS": {
                "transaction_mode": "IMMEDIATE",
                "timeout": 20,
                # A larger page cache (32 MB instead of 2) spares SQLCipher decrypting the same pages again;
                # sorting and grouping of long lists stay in memory.
                "init_command": "PRAGMA journal_mode=WAL; PRAGMA synchronous=NORMAL; PRAGMA cache_size=-32000; PRAGMA temp_store=MEMORY;",
            },
            # A file like the client's, because the in-memory test database locks whole tables.
            "TEST": {"NAME": str(Path(tempfile.gettempdir()) / "schule-test.sqlite3")},
            # Each thread keeps its connection. Opening and closing connections to one file from
            # several threads at once deadlocked the client's sidecar in SQLite's file locks.
            "CONN_MAX_AGE": None,
        }
    from urllib.parse import unquote, urlparse

    u = urlparse(url)
    return {
        "ENGINE": "django.db.backends.postgresql",
        "NAME": u.path.lstrip("/"),
        "USER": unquote(u.username or ""),
        "PASSWORD": unquote(u.password or ""),
        "HOST": u.hostname or "",
        "PORT": str(u.port or ""),
        "CONN_MAX_AGE": 60,
    }


DATABASES = {"default": _database()}

AUTH_USER_MODEL = "core.User"
LOGIN_URL = "login"
LOGIN_REDIRECT_URL = "dashboard"
LOGOUT_REDIRECT_URL = "login"

PASSWORD_HASHERS = [
    "django.contrib.auth.hashers.Argon2PasswordHasher",
    "django.contrib.auth.hashers.PBKDF2PasswordHasher",
]
AUTH_PASSWORD_VALIDATORS = [
    {"NAME": "django.contrib.auth.password_validation.MinimumLengthValidator", "OPTIONS": {"min_length": 12}},
    {"NAME": "django.contrib.auth.password_validation.CommonPasswordValidator"},
]
AUTHENTICATION_BACKENDS = ["django.contrib.auth.backends.ModelBackend"]

LANGUAGE_CODE = "de"
# The interface language. Names a school gives its fields and masks keep the language they were
# typed in, unless someone renames them for one language (masks/i18n.py).
LANGUAGES = [("de", "Deutsch"), ("en", "English"), ("zh-hans", "简体中文"), ("zh-hant", "繁體中文")]
LOCALE_PATHS = [BASE_DIR / "locale"]
LANGUAGE_COOKIE_NAME = "schule_language"
LANGUAGE_COOKIE_AGE = 60 * 60 * 24 * 365
LANGUAGE_COOKIE_SAMESITE = "Lax"
def _time_zone():
    """TIME_ZONE as set (the desktop app passes the computer's own zone); Europe/Berlin otherwise or if unknown."""
    import zoneinfo

    name = os.environ.get("TIME_ZONE") or "Europe/Berlin"
    try:
        zoneinfo.ZoneInfo(name)
    except (zoneinfo.ZoneInfoNotFoundError, ValueError):
        return "Europe/Berlin"
    return name


TIME_ZONE = _time_zone()
USE_I18N = True
USE_TZ = True

STATIC_URL = "static/"
STATICFILES_DIRS = [BASE_DIR / "static"]
STATIC_ROOT = BASE_DIR / "staticfiles"
MEDIA_URL = "media/"
MEDIA_ROOT = Path(os.environ.get("MEDIA_ROOT", BASE_DIR / "media"))
# Set by the desktop client from the Keychain, so uploads on a laptop are encrypted like its database.
MEDIA_KEY = os.environ.get("MEDIA_KEY", "")
STORAGES = {
    "default": {"BACKEND": "core.sealed.SealedStorage" if MEDIA_KEY else "django.core.files.storage.FileSystemStorage"},
    # Hashed names need collectstatic, which the image runs at build time.
    "staticfiles": {"BACKEND": "whitenoise.storage.CompressedStaticFilesStorage" if DEBUG or os.environ.get("TESTING") else "core.storage.VersionedStaticFilesStorage"},
}
WHITENOISE_MIMETYPES = {".mjs": "text/javascript"}

DEFAULT_AUTO_FIELD = "django.db.models.BigAutoField"
DATA_UPLOAD_MAX_NUMBER_FIELDS = 20000
FILE_UPLOAD_MAX_MEMORY_SIZE = 5 * 1024 * 1024
MAX_UPLOAD_BYTES = 20 * 1024 * 1024
MAX_BODY_BYTES = 64 * 1024 * 1024
ANONYMOUS_BODY_BYTES = 1024 * 1024
if MEDIA_KEY:
    # With encrypted uploads, a large file must not wait in plain text in the temp folder.
    FILE_UPLOAD_MAX_MEMORY_SIZE = MAX_BODY_BYTES
    FILE_UPLOAD_HANDLERS = ["django.core.files.uploadhandler.MemoryFileUploadHandler"]

# Session and cookie hardening
SESSION_COOKIE_AGE = 60 * 60 * 10
SESSION_COOKIE_HTTPONLY = True
SESSION_COOKIE_SAMESITE = "Lax"
CSRF_COOKIE_SAMESITE = "Lax"
SECURE_CONTENT_TYPE_NOSNIFF = True
X_FRAME_OPTIONS = "DENY"
CSRF_FAILURE_VIEW = "core.views.csrf_failure"
if not DEBUG and not os.environ.get("TESTING"):
    SESSION_COOKIE_SECURE = env_bool("SECURE_COOKIES", True)
    CSRF_COOKIE_SECURE = env_bool("SECURE_COOKIES", True)
    SECURE_PROXY_SSL_HEADER = ("HTTP_X_FORWARDED_PROTO", "https")
    SECURE_SSL_REDIRECT = env_bool("SECURE_COOKIES", True)
    SECURE_REDIRECT_EXEMPT = [r"^healthz/$"]  # The container asks itself over plain http.
    SECURE_HSTS_SECONDS = int(os.environ.get("HSTS_SECONDS", 30 * 24 * 3600)) if env_bool("SECURE_COOKIES", True) else 0

# Application settings
VAULT_KEY = os.environ.get("VAULT_KEY", "")
VAULT_KEY_FILE = os.environ.get("VAULT_KEY_FILE", str(BASE_DIR / ".secrets" / "vault.key"))
REQUIRE_2FA = env_bool("REQUIRE_2FA", True)
SERVER_ID = os.environ.get("SERVER_ID", "eu")
# Each server draws device numbers from its own range, so two-way sync cannot collide.
DEVICE_NUMBER_STEP = int(os.environ.get("DEVICE_NUMBER_STEP", "2"))
DEVICE_NUMBER_OFFSET = int(os.environ.get("DEVICE_NUMBER_OFFSET", "0"))
APP_VERSION = (os.environ.get("SOURCE_COMMIT") or os.environ.get("APP_VERSION") or "dev")[:7]
# The DataFluss version, the same for server, clients and modules (VERSION, scripts/set-version.sh).
# APP_VERSION above is the commit, for cache-busting and the server's update button.
DATAFLUSS_VERSION = (BASE_DIR / "VERSION").read_text().strip() if (BASE_DIR / "VERSION").exists() else "dev"
BACKUP_ROOT = Path(os.environ.get("BACKUP_ROOT", BASE_DIR / "backups"))
# The nightly backup. Without a password it stays off, without a bucket it stays on this server.
BACKUP_PASSWORD = os.environ.get("BACKUP_PASSWORD", "")
BACKUP_S3_ENDPOINT = os.environ.get("BACKUP_S3_ENDPOINT", "")
BACKUP_S3_BUCKET = os.environ.get("BACKUP_S3_BUCKET", "")
BACKUP_S3_PREFIX = os.environ.get("BACKUP_S3_PREFIX", "")
BACKUP_S3_REGION = os.environ.get("BACKUP_S3_REGION", "")
BACKUP_S3_ACCESS_KEY = os.environ.get("BACKUP_S3_ACCESS_KEY", "")
BACKUP_S3_SECRET_KEY = os.environ.get("BACKUP_S3_SECRET_KEY", "")
# In the image /data must be a volume, because anything else disappears with the next update.
REQUIRE_DATA_VOLUME = env_bool("REQUIRE_DATA_VOLUME", False)
# Says that this installation holds no real people. Only then can seed_demo run.
ALLOW_DEMO_DATA = env_bool("ALLOW_DEMO_DATA", DEBUG or bool(os.environ.get("TESTING")))

# Update button. The app only talks to the Coolify API and never touches Docker.
COOLIFY_API_URL = os.environ.get("COOLIFY_API_URL", "").rstrip("/")
COOLIFY_API_TOKEN = os.environ.get("COOLIFY_API_TOKEN", "")
COOLIFY_DASHBOARD_URL = os.environ.get("COOLIFY_DASHBOARD_URL", "").rstrip("/")
COOLIFY_APP_UUID = os.environ.get("COOLIFY_APP_UUID") or os.environ.get("COOLIFY_RESOURCE_UUID", "")
GITHUB_REPOSITORY = os.environ.get("GITHUB_REPOSITORY", "rana-gmbh/DataFluss")
# What the update button compares: "releases" (the version against the latest release) or "commits" (the
# running commit against the newest one of GITHUB_BRANCH).
UPDATE_SOURCE = os.environ.get("UPDATE_SOURCE", "releases")
GITHUB_BRANCH = os.environ.get("GITHUB_BRANCH", "main")
# The template catalog (tables/catalog.py, docs/website.md): where "Von datafluss.de" and the daily check for newer
# versions ask. Empty switches the catalog off for this server.
TEMPLATE_CATALOG_URL = os.environ.get("TEMPLATE_CATALOG_URL", "https://vorlagen.datafluss.de").rstrip("/")
GITHUB_TOKEN = os.environ.get("GITHUB_TOKEN", "")
CARD_INPUT_FORMAT = os.environ.get("CARD_INPUT_FORMAT", "hex")  # hex | dec
REVEAL_LIMIT_PER_MINUTE = int(os.environ.get("REVEAL_LIMIT_PER_MINUTE", "30"))

# Microsoft 365 sign-in is active as soon as these are set
OIDC_RP_CLIENT_ID = os.environ.get("OIDC_CLIENT_ID", "")
OIDC_RP_CLIENT_SECRET = os.environ.get("OIDC_CLIENT_SECRET", "")
OIDC_TENANT = os.environ.get("OIDC_TENANT", "common")
OIDC_AUTHORITY = os.environ.get("OIDC_AUTHORITY", "https://login.microsoftonline.com")
OIDC_ENABLED = bool(OIDC_RP_CLIENT_ID and OIDC_RP_CLIENT_SECRET)
if OIDC_ENABLED:
    # Other tenants could otherwise sign in with a matching e-mail address.
    if not re.fullmatch(r"[0-9a-fA-F-]{36}", OIDC_TENANT):
        raise RuntimeError("OIDC_TENANT must be the school's tenant ID, not a shared endpoint.")
    INSTALLED_APPS.append("mozilla_django_oidc")
    AUTHENTICATION_BACKENDS.insert(0, "core.oidc.ExistingUsersOnlyBackend")
    _base = f"{OIDC_AUTHORITY}/{OIDC_TENANT}"
    OIDC_OP_AUTHORIZATION_ENDPOINT = f"{_base}/oauth2/v2.0/authorize"
    OIDC_OP_TOKEN_ENDPOINT = f"{_base}/oauth2/v2.0/token"
    OIDC_OP_USER_ENDPOINT = "https://graph.microsoft.com/oidc/userinfo"
    OIDC_OP_JWKS_ENDPOINT = f"{_base}/discovery/v2.0/keys"
    OIDC_RP_SIGN_ALGO = "RS256"
    OIDC_RP_SCOPES = "openid email profile"
    LOGIN_REDIRECT_URL_FAILURE = "/anmelden/"

LOGGING = {
    "version": 1,
    "disable_existing_loggers": False,
    "handlers": {"console": {"class": "logging.StreamHandler"}},
    "root": {"handlers": ["console"], "level": "WARNING"},
}
