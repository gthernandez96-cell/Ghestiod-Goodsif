import sys
import os
import shutil
import zipfile

# 1. Rutas en PythonAnywhere
home_dir = "/home/gthernandez96"
zip_path = os.path.join(home_dir, "pos-empresarial.zip")
project_path = os.path.join(home_dir, "pos-empresarial")
backend_path = os.path.join(project_path, "backend")
user_site = "/home/gthernandez96/.local/lib/python3.10/site-packages"

# 2. Agregar user site-packages PRIMERO
if user_site not in sys.path:
    sys.path.insert(0, user_site)

# 3. Eliminar paquetes locales que interfieren
bad_dirs = [
    "fastapi", "pydantic", "pydantic_core", "pydantic_settings",
    "a2wsgi", "starlette", "annotated_types", "typing_inspection",
    "aiosqlite", "dotenv"
]
for bad in bad_dirs:
    bad_path = os.path.join(backend_path, bad)
    if os.path.exists(bad_path):
        shutil.rmtree(bad_path, ignore_errors=True)

# 4. Descomprimir ZIP si existe
if os.path.exists(zip_path):
    try:
        os.makedirs(project_path, exist_ok=True)
        with zipfile.ZipFile(zip_path, 'r') as zip_ref:
            zip_ref.extractall(project_path)
        try:
            os.remove(zip_path)
        except Exception:
            pass
    except Exception as e:
        sys.stderr.write(f"⚠️ Error unzipping: {e}\n")
    for bad in bad_dirs:
        bad_path = os.path.join(backend_path, bad)
        if os.path.exists(bad_path):
            shutil.rmtree(bad_path, ignore_errors=True)

# 5. Configurar PYTHONPATH
if backend_path not in sys.path:
    sys.path.insert(0, backend_path)
if project_path not in sys.path:
    sys.path.insert(0, project_path)

# 6. Variables de entorno
os.environ["DATABASE_URL"] = f"sqlite+aiosqlite:///{project_path}/pos.db"
os.environ["DATABASE_URL_SYNC"] = f"sqlite:///{project_path}/pos.db"
os.environ["SECRET_KEY"] = "clave-secreta-pos-empresarial-2026-prod"

# 7. Usar a2wsgi para adaptar FastAPI ASGI -> WSGI
import asyncio
try:
    asyncio.set_event_loop_policy(asyncio.DefaultEventLoopPolicy())
except Exception:
    pass

from app.main import app as fastapi_app
from a2wsgi import ASGIMiddleware

# Handler WSGI principal
application = ASGIMiddleware(fastapi_app)

