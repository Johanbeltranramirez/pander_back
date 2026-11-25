import os
from pathlib import Path

# Build paths inside the project like this: BASE_DIR / 'subdir'.
BASE_DIR = Path(__file__).resolve().parent.parent


# ── Configuración por variables de entorno ────────────────────
# En local se pueden poner en un archivo .env (ver .env.example).
# En Kubernetes llegan desde el ConfigMap y el Secret.
def _cargar_dotenv(ruta):
    # Lector mínimo de .env (así no hace falta instalar otra librería)
    if not ruta.exists():
        return
    for linea in ruta.read_text(encoding='utf-8').splitlines():
        linea = linea.strip()
        if not linea or linea.startswith('#') or '=' not in linea:
            continue
        clave, valor = linea.split('=', 1)
        os.environ.setdefault(clave.strip(), valor.strip().strip('"').strip("'"))


_cargar_dotenv(BASE_DIR / '.env')


def _bool(nombre, defecto=False):
    return os.environ.get(nombre, str(defecto)).strip().lower() in ('1', 'true', 'yes', 'si', 'on')


def _lista(nombre, defecto=''):
    return [x.strip() for x in os.environ.get(nombre, defecto).split(',') if x.strip()]


# SECURITY WARNING: la clave y el modo debug ya NO van en el código.
DEBUG = _bool('DJANGO_DEBUG', False)

SECRET_KEY = os.environ.get('DJANGO_SECRET_KEY', '')
if not SECRET_KEY:
    if DEBUG:
        SECRET_KEY = 'django-insecure-solo-para-desarrollo-local'
    else:
        raise RuntimeError('Falta la variable de entorno DJANGO_SECRET_KEY')

ALLOWED_HOSTS = _lista('DJANGO_ALLOWED_HOSTS', 'localhost,127.0.0.1' if DEBUG else '')


# Application definition

INSTALLED_APPS = [
    'django.contrib.admin',
    'django.contrib.auth',
    'django.contrib.contenttypes',
    'django.contrib.sessions',
    'django.contrib.messages',
    'django.contrib.staticfiles',
    'apps.acceso',
    'apps.empresa',
    'apps.vacantes',
    'apps.candidatos',
    'apps.evaluacion',
    'corsheaders',
    'rest_framework'
]

MIDDLEWARE = [
    'corsheaders.middleware.CorsMiddleware',
    'django.middleware.security.SecurityMiddleware',
    'whitenoise.middleware.WhiteNoiseMiddleware',   # sirve /static (CSS del admin) desde gunicorn
    'django.contrib.sessions.middleware.SessionMiddleware',
    'django.middleware.common.CommonMiddleware',
    'django.middleware.csrf.CsrfViewMiddleware',
    'django.contrib.auth.middleware.AuthenticationMiddleware',
    'django.contrib.messages.middleware.MessageMiddleware',
    'django.middleware.clickjacking.XFrameOptionsMiddleware',
]

ROOT_URLCONF = 'config.urls'

TEMPLATES = [
    {
        'BACKEND': 'django.template.backends.django.DjangoTemplates',
        'DIRS': [],
        'APP_DIRS': True,
        'OPTIONS': {
            'context_processors': [
                'django.template.context_processors.request',
                'django.contrib.auth.context_processors.auth',
                'django.contrib.messages.context_processors.messages',
            ],
        },
    },
]

WSGI_APPLICATION = 'config.wsgi.application'


# Database
# https://docs.djangoproject.com/en/6.0/ref/settings/#databases

DATABASES = {
    'default': {
        'ENGINE': 'django.db.backends.postgresql',
        'NAME': os.environ.get('DB_NAME', 'pander_db'),
        'USER': os.environ.get('DB_USER', 'postgres'),
        'PASSWORD': os.environ.get('DB_PASSWORD', ''),
        'HOST': os.environ.get('DB_HOST', 'localhost'),
        'PORT': os.environ.get('DB_PORT', '5432'),
        'CONN_MAX_AGE': int(os.environ.get('DB_CONN_MAX_AGE', 60)),
    }
}

# ── URL base del frontend (para generar enlaces de evaluación) ─
FRONTEND_URL = os.environ.get('FRONTEND_URL', 'http://localhost:5173').rstrip('/')
DEFAULT_AUTO_FIELD = 'django.db.models.BigAutoField'

# ── Archivos subidos (CV candidatos) ──────────────────────────
MEDIA_URL  = '/media/'
MEDIA_ROOT = Path(os.environ.get('MEDIA_ROOT', BASE_DIR / 'media'))

# ── CORS / CSRF ───────────────────────────────────────────────
# En producción el frontend y la API comparten dominio (el Ingress enruta /api),
# pero dejamos la lista por si se usa otro origen.
CORS_ALLOWED_ORIGINS = _lista('CORS_ALLOWED_ORIGINS', 'http://localhost:5173,http://127.0.0.1:5173')
CSRF_TRUSTED_ORIGINS = _lista('CSRF_TRUSTED_ORIGINS')

# ── Correo (SMTP) ─────────────────────────────────────────────
EMAIL_BACKEND      = os.environ.get('EMAIL_BACKEND', 'django.core.mail.backends.smtp.EmailBackend')
EMAIL_HOST         = os.environ.get('EMAIL_HOST', 'smtp.gmail.com')
EMAIL_PORT         = int(os.environ.get('EMAIL_PORT', 587))
EMAIL_USE_TLS      = _bool('EMAIL_USE_TLS', True)
EMAIL_USE_SSL      = _bool('EMAIL_USE_SSL', False)
EMAIL_HOST_USER    = os.environ.get('EMAIL_HOST_USER', '')
EMAIL_HOST_PASSWORD = os.environ.get('EMAIL_HOST_PASSWORD', '')
DEFAULT_FROM_EMAIL = os.environ.get('DEFAULT_FROM_EMAIL', 'Pander Notificaciones <no-reply@pander.local>')

# ── DRF ───────────────────────────────────────────────────────
REST_FRAMEWORK = {
    'DEFAULT_RENDERER_CLASSES': [
        'rest_framework.renderers.JSONRenderer',
    ],
    'DEFAULT_PARSER_CLASSES': [
        'rest_framework.parsers.JSONParser',
        'rest_framework.parsers.MultiPartParser',   # ← para subida de archivos
        'rest_framework.parsers.FormParser',
    ],
}

# Password validation
# https://docs.djangoproject.com/en/6.0/ref/settings/#auth-password-validators

AUTH_PASSWORD_VALIDATORS = [
    {
        'NAME': 'django.contrib.auth.password_validation.UserAttributeSimilarityValidator',
    },
    {
        'NAME': 'django.contrib.auth.password_validation.MinimumLengthValidator',
    },
    {
        'NAME': 'django.contrib.auth.password_validation.CommonPasswordValidator',
    },
    {
        'NAME': 'django.contrib.auth.password_validation.NumericPasswordValidator',
    },
]


# Internationalization
# https://docs.djangoproject.com/en/6.0/topics/i18n/

LANGUAGE_CODE = 'en-us'

TIME_ZONE = 'UTC'

USE_I18N = True

USE_TZ = True


# Static files (CSS, JavaScript, Images)
# https://docs.djangoproject.com/en/6.0/howto/static-files/

STATIC_URL = 'static/'
STATIC_ROOT = BASE_DIR / 'staticfiles'   # aquí deja collectstatic los archivos del admin

# ── Seguridad cuando corre detrás del Ingress (HTTPS lo termina Traefik) ──
if not DEBUG:
    SECURE_PROXY_SSL_HEADER = ('HTTP_X_FORWARDED_PROTO', 'https')
    USE_X_FORWARDED_HOST = True
    SECURE_SSL_REDIRECT = _bool('DJANGO_SSL_REDIRECT', True)
    SECURE_REDIRECT_EXEMPT = [r'^api/health/$']   # las probes de K8s llegan por HTTP
    SESSION_COOKIE_SECURE = SECURE_SSL_REDIRECT
    CSRF_COOKIE_SECURE = SECURE_SSL_REDIRECT
    SECURE_HSTS_SECONDS = int(os.environ.get('DJANGO_HSTS_SECONDS', 0))


# ── Importación de hojas de vida con IA (modelo BiLSTM-CRF) ───
# Ruta del checkpoint entrenado (puede sobrescribirse con la variable de entorno CV_MODEL_PATH).
CV_MODEL_PATH = Path(os.environ.get(
    "CV_MODEL_PATH", BASE_DIR / "apps" / "candidatos" / "ia" / "modelo" / "modelo.pt"))
CV_DEVICE         = os.environ.get("CV_DEVICE") or None        # "cpu" | "cuda" | None (automático)
CV_MAX_ARCHIVOS   = int(os.environ.get("CV_MAX_ARCHIVOS", 50))  # archivos por solicitud
CV_MAX_BYTES      = int(os.environ.get("CV_MAX_BYTES", 10 * 1024 * 1024))  # por archivo
CV_MAX_PAGINAS    = int(os.environ.get("CV_MAX_PAGINAS", 10))   # páginas leídas por PDF
CV_CACHE_SEGUNDOS = int(os.environ.get("CV_CACHE_SEGUNDOS", 3600))  # caché de extracciones
