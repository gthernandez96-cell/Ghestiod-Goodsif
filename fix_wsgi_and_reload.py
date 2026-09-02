import httpx

token = "1e628583fd7c3b8aeadcf91b5ae29aa820f6daea"
username = "gthernandez96"
base_url = f"https://www.pythonanywhere.com/api/v0/user/{username}"
headers = {"Authorization": f"Token {token}"}
domain = "gthernandez96.pythonanywhere.com"

wsgi_content = """import sys
import os
import zipfile
import asyncio

username = 'gthernandez96'
project_home = f'/home/{username}/pos-empresarial'
zip_path = f'/home/{username}/pos-empresarial.zip'
db_file = f'{project_home}/pos.db'

# 1. Descomprimir siempre el ZIP más reciente al iniciar
if os.path.exists(zip_path):
    os.makedirs(project_home, exist_ok=True)
    with zipfile.ZipFile(zip_path, 'r') as zip_ref:
        zip_ref.extractall(project_home)

# 2. Paths
backend_path = os.path.join(project_home, 'backend')
if backend_path not in sys.path:
    sys.path.insert(0, backend_path)
if project_home not in sys.path:
    sys.path.insert(0, project_home)

# 3. Variables de entorno
os.environ['ENVIRONMENT'] = 'production'
os.environ['DEBUG'] = 'false'
os.environ['SECRET_KEY'] = 'pos_empresa_prod_key_2026_gt_fel_jwt'
os.environ['DATABASE_URL'] = f'sqlite+aiosqlite:///{db_file}'
os.environ['DATABASE_URL_SYNC'] = f'sqlite:///{db_file}'

from app.main import app as asgi_app
from a2wsgi import ASGIMiddleware

application = ASGIMiddleware(asgi_app)
"""

with httpx.Client(verify=False, timeout=60.0) as client:
    # 1. Subir WSGI
    wsgi_url = f"{base_url}/files/path/var/www/{username}_pythonanywhere_com_wsgi.py"
    r = client.post(wsgi_url, files={"content": (f"{username}_pythonanywhere_com_wsgi.py", wsgi_content.encode("utf-8"), "text/plain")}, headers=headers)
    print("WSGI upload:", r.status_code)

    # 2. Recargar WebApp
    r_reload = client.post(f"{base_url}/webapps/{domain}/reload/", headers=headers)
    print("Reload Status:", r_reload.status_code)
