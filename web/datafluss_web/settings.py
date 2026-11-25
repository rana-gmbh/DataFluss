"""Settings of the template site. Everything that differs between a computer and the server comes from the
environment (docs/website.md, "Betrieb")."""
import os
import sys
from pathlib import Path
from urllib.parse import urlparse

BASE_DIR = Path(__file__).resolve().parent.parent
DATA_DIR = Path(os.environ.get("DATA_DIR", BASE_DIR / "data"))


def env_bool(name, default=False):
    return os.environ.get(name, "1" if default else "0").lower() in ("1", "true", "yes")


DEBUG = env_bool("DEBUG")
TESTING = env_bool("TESTING") or sys.argv[1:2] == ["test"]
SECRET_KEY = os.environ.get("SECRET_KEY") or ("dev-only-not-secret" if DEBUG or TESTING else "")
if not SECRET_KEY:
    raise RuntimeError("SECRET_KEY fehlt.")
SITE_URL = os.environ.get("SITE_URL", "http://127.0.0.1:8140").rstrip("/")
# Websites people publish from DataFluss live on a host of their own (seiten/hosting.py), apart from the accounts.
SITES_URL = os.environ.get("SITES_URL", "http://databases.localhost:8140").rstrip("/")
# datafluss.de itself: the product's main page (home/), www redirects to it.
HOME_URL = os.environ.get("HOME_URL", "http://datafluss.localhost:8140").rstrip("/")
SITES_QUOTA_MB = int(os.environ.get("SITES_QUOTA_MB", "200"))
SITES_PER_ACCOUNT = int(os.environ.get("SITES_PER_ACCOUNT", "3"))
ALLOWED_HOSTS = [h for h in os.environ.get("ALLOWED_HOSTS", "").split(",") if h] + [urlparse(SITE_URL).hostname, urlparse(SITES_URL).hostname, urlparse(HOME_URL).hostname,
                                                                                     "www." + (urlparse(HOME_URL).hostname or ""),
                                                                                     "127.0.0.1", "localhost"]
CSRF_TRUSTED_ORIGINS = [SITE_URL]
# Behind Coolify's proxy the client's address is the first one in X-Forwarded-For (konten/limits.py).
TRUST_PROXY = env_bool("TRUST_PROXY")

INSTALLED_APPS = [
    "konten",  # first, so its createsuperuser (address and name, no password) replaces Django's
    "django.contrib.auth",
    "django.contrib.contenttypes",
    "django.contrib.sessions",
    "django.contrib.messages",
    "django.contrib.staticfiles",
    "katalog",
    "seiten",
    "home",
]
MIDDLEWARE = [
    "django.middleware.security.SecurityMiddleware",
    "seiten.hosting.SitesHostMiddleware",  # the sites' own host: hosted files and the hosting API, no sessions
    "home.middleware.HomeHostMiddleware",  # datafluss.de: the main page with URLs of its own
    "whitenoise.middleware.WhiteNoiseMiddleware",
    "django.contrib.sessions.middleware.SessionMiddleware",
    "django.middleware.locale.LocaleMiddleware",  # German, or English from the switch or the browser
    "django.middleware.common.CommonMiddleware",
    "django.middleware.csrf.CsrfViewMiddleware",
    "django.contrib.auth.middleware.AuthenticationMiddleware",
    "django.contrib.messages.middleware.MessageMiddleware",
    "django.middleware.clickjacking.XFrameOptionsMiddleware",
    "konten.middleware.SecurityHeadersMiddleware",
]
ROOT_URLCONF = "datafluss_web.urls"
WSGI_APPLICATION = "datafluss_web.wsgi.application"
TEMPLATES = [{
    "BACKEND": "django.template.backends.django.DjangoTemplates",
    "DIRS": [BASE_DIR / "templates"],
    "APP_DIRS": True,
    "OPTIONS": {"context_processors": [
        "django.template.context_processors.request",
        "django.contrib.auth.context_processors.auth",
        "django.contrib.messages.context_processors.messages",
        "katalog.context.site",
        "seiten.context.waiting",
    ]},
}]


def _database():
    url = os.environ.get("DATABASE_URL")
    if not url:
        DATA_DIR.mkdir(parents=True, exist_ok=True)
        return {"ENGINE": "django.db.backends.sqlite3", "NAME": DATA_DIR / "web.sqlite3"}
    parts = urlparse(url)
    return {"ENGINE": "django.db.backends.postgresql", "NAME": parts.path.lstrip("/"), "USER": parts.username,
            "PASSWORD": parts.password, "HOST": parts.hostname, "PORT": parts.port or 5432, "CONN_MAX_AGE": 60}


DATABASES = {"default": _database()}
DEFAULT_AUTO_FIELD = "django.db.models.BigAutoField"
AUTH_USER_MODEL = "konten.User"
AUTHENTICATION_BACKENDS = ["django.contrib.auth.backends.ModelBackend"]
LOGIN_URL = "login"

LANGUAGE_CODE = "de"
LANGUAGES = [("de", "Deutsch"), ("en", "English")]
LANGUAGE_COOKIE_NAME = "dfweb_language"
LANGUAGE_COOKIE_SAMESITE = "Lax"
# The site's own texts, then the platform's (the template check's messages, tables/template_check.py).
LOCALE_PATHS = [BASE_DIR / "locale", BASE_DIR.parent / "locale"]
TIME_ZONE = "Europe/Berlin"
USE_I18N = True
USE_TZ = True

STATIC_URL = "/static/"
STATIC_ROOT = BASE_DIR / "staticfiles"
STATICFILES_DIRS = [BASE_DIR / "static"]
STORAGES = {
    "default": {"BACKEND": "django.core.files.storage.FileSystemStorage"},
    "staticfiles": {"BACKEND": "django.contrib.staticfiles.storage.StaticFilesStorage" if DEBUG or TESTING
                    else "whitenoise.storage.CompressedManifestStaticFilesStorage"},
}
MEDIA_ROOT = Path(os.environ.get("MEDIA_ROOT", DATA_DIR / "media"))
DATA_UPLOAD_MAX_MEMORY_SIZE = 2 * 1024 * 1024
FILE_UPLOAD_MAX_MEMORY_SIZE = 2 * 1024 * 1024

# Own cookie names, so a DataFluss server on the same host (127.0.0.1 while developing) keeps its sign-in.
SESSION_COOKIE_NAME = "dfweb_session"
CSRF_COOKIE_NAME = "dfweb_csrf"
SESSION_COOKIE_HTTPONLY = True
SESSION_COOKIE_SAMESITE = "Lax"
SESSION_COOKIE_AGE = 30 * 24 * 3600
SESSION_COOKIE_SECURE = CSRF_COOKIE_SECURE = SITE_URL.startswith("https://")
SECURE_CONTENT_TYPE_NOSNIFF = True
SECURE_REFERRER_POLICY = "same-origin"
X_FRAME_OPTIONS = "DENY"
if SITE_URL.startswith("https://"):
    SECURE_PROXY_SSL_HEADER = ("HTTP_X_FORWARDED_PROTO", "https")
    SECURE_HSTS_SECONDS = 31536000
    SECURE_SSL_REDIRECT = True
    SECURE_REDIRECT_EXEMPT = [r"^healthz/$"]  # the container's own health check asks over plain http
# HSTS for every subdomain of datafluss.de and the preload list are decisions for the whole domain, not this site.
SILENCED_SYSTEM_CHECKS = ["security.W005", "security.W021"]

# Mail: sign-in links and decisions. Without a host (on a computer) they are written to the console.
EMAIL_HOST = os.environ.get("EMAIL_HOST", "")
EMAIL_PORT = int(os.environ.get("EMAIL_PORT", "587"))
EMAIL_HOST_USER = os.environ.get("EMAIL_HOST_USER", "")
EMAIL_HOST_PASSWORD = os.environ.get("EMAIL_HOST_PASSWORD", "")
EMAIL_USE_SSL = env_bool("EMAIL_USE_SSL")  # port 465; otherwise STARTTLS on 587
EMAIL_USE_TLS = env_bool("EMAIL_USE_TLS", True) and not EMAIL_USE_SSL
EMAIL_TIMEOUT = 20
DEFAULT_FROM_EMAIL = os.environ.get("DEFAULT_FROM_EMAIL", "DataFluss Templates <templates@datafluss.de>")
EMAIL_BACKEND = ("django.core.mail.backends.smtp.EmailBackend" if EMAIL_HOST else "django.core.mail.backends.console.EmailBackend")
# Who hears of new uploads and reports (comma-separated); empty: nobody, the queue is still there.
REVIEW_NOTIFY = [a for a in os.environ.get("REVIEW_NOTIFY", "").split(",") if a]

LOGGING = {"version": 1, "disable_existing_loggers": False, "handlers": {"console": {"class": "logging.StreamHandler"}},
           "root": {"handlers": ["console"], "level": "WARNING"}}
