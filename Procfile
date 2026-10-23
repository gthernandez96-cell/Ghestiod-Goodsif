web: python manage.py migrate && gunicorn wsgi:application --workers 3 --threads 2 --bind 0.0.0.0:${PORT:-8080}
