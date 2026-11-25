# ── Imagen del backend de Pander (Django + Gunicorn) ──────────
# Se construye para ARM64 (Oracle Ampere A1). En GitHub Actions se usa un runner ARM nativo.
FROM python:3.12-slim

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PIP_NO_CACHE_DIR=1 \
    PIP_DISABLE_PIP_VERSION_CHECK=1

# libglib2.0-0 lo piden OpenCV/EasyOCR; el resto de dependencias trae wheels propios
RUN apt-get update \
 && apt-get install -y --no-install-recommends libglib2.0-0 \
 && rm -rf /var/lib/apt/lists/*

WORKDIR /app

# Primero solo requirements: así Docker reutiliza la capa si el código cambia pero las librerías no
COPY requirements.txt .
RUN pip install -r requirements.txt

COPY . .

# Archivos estáticos del admin de Django (los sirve WhiteNoise).
# La clave es falsa y solo existe durante este paso del build.
RUN DJANGO_SECRET_KEY=solo-para-build python manage.py collectstatic --noinput \
 && mkdir -p /app/media \
 && useradd --uid 1000 --create-home appuser \
 && chown -R appuser:appuser /app/media /app/staticfiles

USER appuser

EXPOSE 8000

# gthread: varios hilos por worker. El timeout es largo porque leer CVs con OCR/IA puede tardar.
CMD ["sh", "-c", "exec gunicorn config.wsgi:application --bind 0.0.0.0:8000 --workers ${GUNICORN_WORKERS:-2} --worker-class gthread --threads ${GUNICORN_THREADS:-4} --timeout ${GUNICORN_TIMEOUT:-300} --access-logfile -"]
