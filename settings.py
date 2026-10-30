"""
Django & Production Settings Configuration for DigitalOcean App Platform.
Soporta despliegue híbrido en DigitalOcean (PostgreSQL Dev Database + Spaces S3)
y desarrollo local automático sin configuraciones manuales.
"""
import os
from pathlib import Path
import dj_database_url

# Build paths inside the project like this: BASE_DIR / 'subdir'.
BASE_DIR = Path(__file__).resolve().parent

# ===== SEGURIDAD & ENTORNO =====
# Lee SECRET_KEY de variable de entorno o usa la clave actual por defecto para desarrollo local
SECRET_KEY = os.environ.get("SECRET_KEY", "clave-secreta-pos-2024")

# DEBUG es False en producción por defecto, pero True en local si no se define DATABASE_URL
if "DEBUG" in os.environ:
    DEBUG = os.environ.get("DEBUG", "False").lower() in ("true", "1", "yes")
else:
    DEBUG = "DATABASE_URL" not in os.environ

# Hosts permitidos
ALLOWED_HOSTS = [
    ".ondigitalocean.app",
    "productosautomotricesdelsur.com",
    "www.productosautomotricesdelsur.com",
    ".productosautomotricesdelsur.com",
    "localhost",
    "127.0.0.1",
]
extra_hosts = os.environ.get("DJANGO_ALLOWED_HOSTS", "")
if extra_hosts:
    for host in extra_hosts.split(","):
        clean_host = host.strip()
        if clean_host and clean_host not in ALLOWED_HOSTS:
            ALLOWED_HOSTS.append(clean_host)

# CSRF Trusted Origins para DigitalOcean y dominios personalizados
CSRF_TRUSTED_ORIGINS = [
    "https://*.ondigitalocean.app",
    "https://productosautomotricesdelsur.com",
    "https://www.productosautomotricesdelsur.com",
]
if extra_hosts:
    for host in extra_hosts.split(","):
        clean_host = host.strip()
        if clean_host:
            origin = clean_host if clean_host.startswith("http") else f"https://{clean_host}"
            if origin not in CSRF_TRUSTED_ORIGINS:
                CSRF_TRUSTED_ORIGINS.append(origin)

# ===== APLICACIONES & MIDDLEWARE =====
INSTALLED_APPS = [
    "django.contrib.auth",
    "django.contrib.contenttypes",
    "django.contrib.sessions",
    "django.contrib.messages",
    "django.contrib.staticfiles",
    "storages",
]

MIDDLEWARE = [
    "django.middleware.security.SecurityMiddleware",
    "whitenoise.middleware.WhiteNoiseMiddleware",
    "django.contrib.sessions.middleware.SessionMiddleware",
    "django.middleware.common.CommonMiddleware",
    "django.middleware.csrf.CsrfViewMiddleware",
    "django.contrib.auth.middleware.AuthenticationMiddleware",
    "django.contrib.messages.middleware.MessageMiddleware",
    "django.middleware.clickjacking.XFrameOptionsMiddleware",
]

ROOT_URLCONF = "urls"

# ===== BASE DE DATOS (OPTIMIZADA PARA DIGITALOCEAN DEV DATABASE $7/mes) =====
# conn_max_age=0 libera inmediatamente las conexiones para no agotar el pool de la Dev Database
if os.environ.get("DATABASE_URL"):
    DATABASES = {
        "default": dj_database_url.config(
            default=os.environ.get("DATABASE_URL"),
            conn_max_age=0,
            ssl_require=True if "ondigitalocean.com" in os.environ.get("DATABASE_URL", "") else False,
        )
    }
else:
    # Entorno local por defecto (mantiene la base de datos de desarrollo sin romper nada)
    DATABASES = {
        "default": {
            "ENGINE": "django.db.backends.sqlite3",
            "NAME": BASE_DIR / "pos.db",
        }
    }

# ===== ARCHIVOS ESTÁTICOS Y MULTIMEDIA (DIGITALOCEAN SPACES $5/mes) =====
STATIC_URL = "/static/"
STATIC_ROOT = BASE_DIR / "staticfiles"

# Directorio de estáticos fuente del proyecto
local_static_dir = BASE_DIR / "frontend" / "static"
STATICFILES_DIRS = [local_static_dir] if local_static_dir.exists() else []

SPACES_ACCESS_KEY = os.environ.get("SPACES_ACCESS_KEY")
SPACES_SECRET_KEY = os.environ.get("SPACES_SECRET_KEY")
SPACES_BUCKET_NAME = os.environ.get("SPACES_BUCKET_NAME")
SPACES_REGION = os.environ.get("SPACES_REGION", "nyc3")

if SPACES_ACCESS_KEY and SPACES_SECRET_KEY and SPACES_BUCKET_NAME:
    # Configuración de almacenamiento en DigitalOcean Spaces (S3 compatible)
    STORAGES = {
        "default": {
            "BACKEND": "storages.backends.s3.S3Storage",
            "OPTIONS": {
                "access_key": SPACES_ACCESS_KEY,
                "secret_key": SPACES_SECRET_KEY,
                "bucket_name": SPACES_BUCKET_NAME,
                "region_name": SPACES_REGION,
                "endpoint_url": f"https://{SPACES_REGION}.digitaloceanspaces.com",
                "default_acl": "public-read",
                "location": "media",
                "querystring_auth": False,
            },
        },
        "staticfiles": {
            "BACKEND": "whitenoise.storage.CompressedManifestStaticFilesStorage",
        },
    }
    MEDIA_URL = f"https://{SPACES_BUCKET_NAME}.{SPACES_REGION}.digitaloceanspaces.com/media/"
else:
    # Almacenamiento local en disco para desarrollo sin Spaces
    STORAGES = {
        "default": {
            "BACKEND": "django.core.files.storage.FileSystemStorage",
        },
        "staticfiles": {
            "BACKEND": "whitenoise.storage.CompressedManifestStaticFilesStorage",
        },
    }
    MEDIA_URL = "/media/"
    MEDIA_ROOT = BASE_DIR / "media"

# ===== CABECERAS DE SEGURIDAD EN PRODUCCIÓN =====
if not DEBUG:
    SECURE_PROXY_SSL_HEADER = ("HTTP_X_FORWARDED_PROTO", "https")
    SESSION_COOKIE_SECURE = True
    CSRF_COOKIE_SECURE = True
    SECURE_BROWSER_XSS_FILTER = True
    SECURE_CONTENT_TYPE_NOSNIFF = True
    X_FRAME_OPTIONS = "SAMEORIGIN"
