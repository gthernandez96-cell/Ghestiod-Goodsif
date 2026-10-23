"""
Gunicorn configuration file for DigitalOcean App Platform and production environments.
Automatically reads the dynamic $PORT assigned by DigitalOcean (default: 8080).
"""
import os

# DigitalOcean asigna el puerto en la variable de entorno $PORT (usualmente 8080)
port = os.environ.get("PORT", "8080")
bind = f"0.0.0.0:{port}"

# Concurrencia de workers
workers = int(os.environ.get("WEB_CONCURRENCY", "3"))
threads = int(os.environ.get("PYTHON_THREADS", "2"))

# Tiempos de espera y estabilidad
timeout = 120
keepalive = 5
graceful_timeout = 30

# Logging a stdout/stderr para captura directa en los Runtime Logs de DigitalOcean
accesslog = "-"
errorlog = "-"
loglevel = "info"
