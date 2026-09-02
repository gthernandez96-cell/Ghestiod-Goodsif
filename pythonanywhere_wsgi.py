import sys
import os
import zipfile
import asyncio

username = 'gthernandez96'
project_home = f'/home/{username}/pos-empresarial'
zip_path = f'/home/{username}/pos-empresarial.zip'

# 1. Determinar ruta del proyecto dinámicamente (PythonAnywhere vs Local)
current_dir = os.path.dirname(os.path.abspath(__file__))
if os.path.exists(project_home):
    base_dir = project_home
else:
    base_dir = current_dir

# Descomprimir siempre el ZIP más reciente si existe
if os.path.exists(zip_path):
    os.makedirs(project_home, exist_ok=True)
    with zipfile.ZipFile(zip_path, 'r') as zip_ref:
        zip_ref.extractall(project_home)

# 2. Agregar backend y raíz a sys.path
backend_path = os.path.join(base_dir, 'backend')
if backend_path not in sys.path:
    sys.path.insert(0, backend_path)
if base_dir not in sys.path:
    sys.path.insert(0, base_dir)

# 3. Variables de entorno
os.environ['ENVIRONMENT'] = 'production'
os.environ['DEBUG'] = 'false'
os.environ['SECRET_KEY'] = 'pos_empresa_prod_key_2026_gt_fel_jwt'
os.environ['DATABASE_URL'] = f'sqlite+aiosqlite:///{project_home}/pos_empresarial.db'

# 4. Configurar política de Event Loop para uWSGI
try:
    asyncio.set_event_loop_policy(asyncio.DefaultEventLoopPolicy())
except Exception:
    pass

from app.main import app as asgi_app
from a2wsgi import ASGIMiddleware

# Handler WSGI principal
application = ASGIMiddleware(asgi_app)
