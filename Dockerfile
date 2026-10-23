FROM python:3.12-slim

WORKDIR /app

# Dependencias del sistema para PostgreSQL, Pillow, Weasyprint y utilidades
RUN apt-get update && apt-get install -y --no-install-recommends \
    gcc \
    libpq-dev \
    libcairo2 \
    libpango-1.0-0 \
    libpangocairo-1.0-0 \
    libgdk-pixbuf2.0-0 \
    libffi-dev \
    shared-mime-info \
    curl \
    && rm -rf /var/lib/apt/lists/*

# Instalar dependencias de Python
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

# Copiar código del proyecto
COPY . .

ENV PYTHONDONTWRITEBYTECODE=1
ENV PYTHONUNBUFFERED=1
ENV PYTHONPATH=/app:/app/backend

# DigitalOcean App Platform usa el puerto $PORT (usualmente 8080)
EXPOSE 8080 8000

CMD ["sh", "-c", "python manage.py migrate && gunicorn wsgi:application --workers 3 --threads 2 --bind 0.0.0.0:${PORT:-8080}"]
