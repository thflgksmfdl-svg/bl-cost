import os
from pathlib import Path
from urllib.parse import parse_qs, unquote, urlparse

BASE_DIR = Path(__file__).resolve().parent.parent
PRODUCTION = os.getenv("RENDER") == "true"
SECRET_KEY = os.getenv("SECRET_KEY", "local-development-only-not-a-production-secret")
DEBUG = not PRODUCTION and os.getenv("DEBUG", "1") == "1"
ALLOWED_HOSTS = ["localhost", "127.0.0.1", "testserver", "bl-cost.onrender.com", "jp-pack.netlify.app"]
if os.getenv("RENDER_EXTERNAL_HOSTNAME"):
    ALLOWED_HOSTS.append(os.environ["RENDER_EXTERNAL_HOSTNAME"])
CSRF_TRUSTED_ORIGINS = [f"https://{h}" for h in ALLOWED_HOSTS if h.endswith((".onrender.com", "jp-pack.netlify.app"))]
INSTALLED_APPS = [
    "django.contrib.admin", "django.contrib.auth", "django.contrib.contenttypes",
    "django.contrib.sessions", "django.contrib.messages", "django.contrib.staticfiles", "ledger",
]
MIDDLEWARE = [
    "django.middleware.security.SecurityMiddleware", "whitenoise.middleware.WhiteNoiseMiddleware",
    "django.contrib.sessions.middleware.SessionMiddleware", "django.middleware.common.CommonMiddleware",
    "django.middleware.csrf.CsrfViewMiddleware", "django.contrib.auth.middleware.AuthenticationMiddleware",
    "django.contrib.messages.middleware.MessageMiddleware", "django.middleware.clickjacking.XFrameOptionsMiddleware",
]
ROOT_URLCONF = "config.urls"
TEMPLATES = [{"BACKEND": "django.template.backends.django.DjangoTemplates", "DIRS": [BASE_DIR / "templates"],
              "APP_DIRS": True, "OPTIONS": {"context_processors": [
                  "django.template.context_processors.request", "django.contrib.auth.context_processors.auth",
                  "django.contrib.messages.context_processors.messages"]}}]
WSGI_APPLICATION = "config.wsgi.application"
db_url = os.getenv("DATABASE_URL", "")
if db_url:
    p = urlparse(db_url)
    options = {"options": "-c search_path=bl_cost", "connect_timeout": 10}
    options.update({k: v[-1] for k, v in parse_qs(p.query).items() if k == "sslmode"})
    DATABASES = {"default": {"ENGINE": "django.db.backends.postgresql", "NAME": unquote(p.path.lstrip("/")),
                 "USER": unquote(p.username or ""), "PASSWORD": unquote(p.password or ""),
                 "HOST": p.hostname, "PORT": p.port or 5432, "CONN_MAX_AGE": 60, "OPTIONS": options}}
else:
    DATABASES = {"default": {"ENGINE": "django.db.backends.sqlite3", "NAME": BASE_DIR / "dev.sqlite3",
                             "OPTIONS": {"timeout": 30}}}
AUTH_PASSWORD_VALIDATORS = [
    {"NAME": "django.contrib.auth.password_validation.UserAttributeSimilarityValidator"},
    {"NAME": "django.contrib.auth.password_validation.MinimumLengthValidator", "OPTIONS": {"min_length": 12}},
    {"NAME": "django.contrib.auth.password_validation.CommonPasswordValidator"},
    {"NAME": "django.contrib.auth.password_validation.NumericPasswordValidator"},
]
LANGUAGE_CODE = "ko-kr"
TIME_ZONE = "Asia/Seoul"
USE_TZ = True
DEFAULT_AUTO_FIELD = "django.db.models.BigAutoField"
STATIC_URL = "/static/"
STATIC_ROOT = BASE_DIR / "staticfiles"
STATICFILES_DIRS = [BASE_DIR / "static"]
STORAGES = {"default": {"BACKEND": "django.core.files.storage.FileSystemStorage"},
            "staticfiles": {"BACKEND": "whitenoise.storage.CompressedManifestStaticFilesStorage"}}
LOGIN_URL = "/login/"
LOGIN_REDIRECT_URL = "/"
LOGOUT_REDIRECT_URL = "/login/"
SESSION_COOKIE_AGE = 60 * 60 * 8
SESSION_COOKIE_HTTPONLY = True
SESSION_COOKIE_SAMESITE = "Lax"
SESSION_COOKIE_SECURE = PRODUCTION
CSRF_COOKIE_SECURE = PRODUCTION
SECURE_PROXY_SSL_HEADER = ("HTTP_X_FORWARDED_PROTO", "https")
SECURE_SSL_REDIRECT = PRODUCTION
SECURE_HSTS_SECONDS = 31536000 if PRODUCTION else 0
SECURE_HSTS_INCLUDE_SUBDOMAINS = True
SECURE_HSTS_PRELOAD = True
SECURE_CONTENT_TYPE_NOSNIFF = True
X_FRAME_OPTIONS = "DENY"
DATA_UPLOAD_MAX_MEMORY_SIZE = 1024 * 1024
SHEET_ID = os.getenv("GOOGLE_SPREADSHEET_ID", "1t_NcYwbYVcP2JGUJnE2tle8vVuZnyhKMFBPHl_0XwHg")
LOGGING = {"version": 1, "disable_existing_loggers": False,
           "handlers": {"console": {"class": "logging.StreamHandler"}},
           "root": {"handlers": ["console"], "level": "INFO"},
           "loggers": {"httpx": {"level": "WARNING"}, "httpcore": {"level": "WARNING"}}}
