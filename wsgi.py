"""
WSGI / ASGI Application Entrypoint for POS Empresarial.
Compatible con Gunicorn en DigitalOcean App Platform, Docker y servidores WSGI/ASGI.
"""
import os
import sys

# Configurar rutas del proyecto
project_root = os.path.dirname(os.path.abspath(__file__))
backend_dir = os.path.join(project_root, "backend")

if backend_dir not in sys.path:
    sys.path.insert(0, backend_dir)
if project_root not in sys.path:
    sys.path.insert(0, project_root)

# Configurar loop de eventos para ejecución asíncrona bajo WSGI
import asyncio
try:
    asyncio.set_event_loop_policy(asyncio.DefaultEventLoopPolicy())
except Exception:
    pass

from app.main import app as fastapi_app
from a2wsgi import ASGIMiddleware

# Handlers exportados para Gunicorn / Uvicorn
application = ASGIMiddleware(fastapi_app)
app = fastapi_app
