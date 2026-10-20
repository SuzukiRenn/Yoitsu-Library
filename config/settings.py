import os
import mimetypes
import re
from pathlib import Path
from django.core.exceptions import ImproperlyConfigured
from .proxy import trusted_proxy_ips

BASE_DIR = Path(__file__).absolute().parent.parent
ENV = os.environ.get('BOOKLIST_ENV', 'production')
if ENV not in ('development', 'production', 'test'):
    raise ImproperlyConfigured('Invalid BOOKLIST_ENV')
DEBUG = ENV == 'development'
SECRET_KEY = os.environ.get('BOOKLIST_SECRET_KEY', '')
if not SECRET_KEY:
    if ENV == 'production':
        raise ImproperlyConfigured('BOOKLIST_SECRET_KEY is required')
    SECRET_KEY = 'local-development-only-not-for-production-' + 'x' * 32
if ENV == 'production' and len(SECRET_KEY) < 50:
    raise ImproperlyConfigured('Production secret must contain at least 50 characters')
ALLOWED_HOSTS = [v.strip() for v in os.environ.get('BOOKLIST_ALLOWED_HOSTS', 'localhost,127.0.0.1').split(',') if v.strip()]
if ENV == 'production' and (not os.environ.get('BOOKLIST_ALLOWED_HOSTS') or '*' in ALLOWED_HOSTS):
    raise ImproperlyConfigured('Explicit production hosts required')
try:
    TRUSTED_PROXY_IPS = trusted_proxy_ips(os.environ.get('BOOKLIST_TRUSTED_PROXY_IPS', ''))
except ValueError:
    raise ImproperlyConfigured('Trusted proxies must be exact IP addresses') from None
ICP_RECORD = os.environ.get('BOOKLIST_ICP_RECORD', '').strip()
if ICP_RECORD and not re.fullmatch(r'[\u4e00-\u9fff]ICP备[0-9]{6,20}号(?:-[0-9]+)?', ICP_RECORD):
    raise ImproperlyConfigured('Invalid ICP record format')
DATA_DIR = Path(os.environ.get('BOOKLIST_DATA_DIR') or BASE_DIR / '.runtime')
if ENV == 'production' and (not os.environ.get('BOOKLIST_DATA_DIR') or not DATA_DIR.is_absolute() or DATA_DIR == BASE_DIR or BASE_DIR in DATA_DIR.parents):
    raise ImproperlyConfigured('Production data directory must be absolute and outside code')
DATA_DIR.mkdir(parents=True, exist_ok=True)
COVER_CACHE_DIR = DATA_DIR / 'covers'
INSTALLED_APPS = ['django.contrib.staticfiles', 'booklist.apps.BooklistConfig']
MIDDLEWARE = ['booklist.proxy.TrustedProxyMiddleware', 'django.middleware.security.SecurityMiddleware', 'django.middleware.clickjacking.XFrameOptionsMiddleware', 'django.middleware.common.CommonMiddleware',
              'django.middleware.csrf.CsrfViewMiddleware', 'booklist.middleware.IdentityMiddleware']
ROOT_URLCONF = 'config.urls'
WSGI_APPLICATION = 'config.wsgi.application'
TEMPLATES = [{'BACKEND': 'django.template.backends.django.DjangoTemplates', 'DIRS': [BASE_DIR / 'templates'], 'APP_DIRS': True,
    'OPTIONS': {'context_processors': ['booklist.context_processors.site_legal']}}]
DATABASES = {'default': {'ENGINE': 'django.db.backends.sqlite3', 'NAME': DATA_DIR / 'booklist.sqlite3',
    'OPTIONS': {'timeout': 5, 'transaction_mode': 'IMMEDIATE'},
    'TEST': {'NAME': str(DATA_DIR / 'test_booklist.sqlite3')}}}
USE_TZ = True
TIME_ZONE = 'Asia/Shanghai'
LANGUAGE_CODE = 'zh-hans'
DEFAULT_AUTO_FIELD = 'django.db.models.BigAutoField'
AUTH_PASSWORD_VALIDATORS = [{'NAME': 'django.contrib.auth.password_validation.MinimumLengthValidator', 'OPTIONS': {'min_length': 12}},
    {'NAME': 'django.contrib.auth.password_validation.CommonPasswordValidator'},
    {'NAME': 'django.contrib.auth.password_validation.NumericPasswordValidator'}]
STATIC_URL = '/static/'
# Some Windows MIME registries label .mjs as text/plain; modules must be JS.
mimetypes.add_type('text/javascript', '.mjs')
STATICFILES_DIRS = [BASE_DIR / 'static']
STATIC_ROOT = BASE_DIR / 'staticfiles'
COOKIE_SECURE = ENV == 'production'
CSRF_COOKIE_SECURE = COOKIE_SECURE
CSRF_COOKIE_HTTPONLY = False
CSRF_COOKIE_SAMESITE = 'Lax'
CSRF_FAILURE_VIEW = 'booklist.views.csrf_failure'
SECURE_SSL_REDIRECT = COOKIE_SECURE
SECURE_HSTS_SECONDS = 31536000 if COOKIE_SECURE else 0
SECURE_HSTS_INCLUDE_SUBDOMAINS = COOKIE_SECURE
SECURE_HSTS_PRELOAD = False
SECURE_CONTENT_TYPE_NOSNIFF = True
DATA_UPLOAD_MAX_MEMORY_SIZE = 32768
ANONYMOUS_COOKIE = 'booklist_anonymous'
ADMIN_COOKIE = 'booklist_admin'
ANONYMOUS_AGE = 365 * 86400
ADMIN_AGE = 12 * 3600
IDEMPOTENCY_AGE = 86400
