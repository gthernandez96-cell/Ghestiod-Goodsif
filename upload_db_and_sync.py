import os
import httpx

token = "1e628583fd7c3b8aeadcf91b5ae29aa820f6daea"
username = "gthernandez96"
base_url = f"https://www.pythonanywhere.com/api/v0/user/{username}"
headers = {"Authorization": f"Token {token}"}
domain = "gthernandez96.pythonanywhere.com"

# 1. Leer base de datos local inicializada con todos los usuarios, empresas y productos
db_path = os.path.abspath("backend/pos.db")
if not os.path.exists(db_path):
    db_path = os.path.abspath("pos.db")

print(f"📦 Leyendo base de datos local: {db_path} ({os.path.getsize(db_path)} bytes)...")
with open(db_path, "rb") as f:
    db_bytes = f.read()

# 2. Subir pos.db a PythonAnywhere
print("☁️ Subiendo pos.db a PythonAnywhere...")
with httpx.Client(verify=False, timeout=60.0) as client:
    dest_urls = [
        f"{base_url}/files/path/home/{username}/pos-empresarial/pos.db",
        f"{base_url}/files/path/home/{username}/pos-empresarial/backend/pos.db",
        f"{base_url}/files/path/home/{username}/pos.db"
    ]
    for url in dest_urls:
        r = client.post(url, files={"content": ("pos.db", db_bytes, "application/octet-stream")}, headers=headers)
        print(f"Uploaded to {url.split('path')[-1]}: Status {r.status_code}")

    # 3. Actualizar WSGI para apuntar exactamente a esta base de datos
    wsgi_content = f"""import sys
import os
import asyncio

username = '{username}'
project_home = f'/home/{{username}}/pos-empresarial'
db_file = f'{{project_home}}/pos.db'

backend_path = os.path.join(project_home, 'backend')
if backend_path not in sys.path:
    sys.path.insert(0, backend_path)
if project_home not in sys.path:
    sys.path.insert(0, project_home)

os.environ['ENVIRONMENT'] = 'production'
os.environ['DEBUG'] = 'false'
os.environ['SECRET_KEY'] = 'pos_empresa_prod_key_2026_gt_fel_jwt'
os.environ['DATABASE_URL'] = f'sqlite+aiosqlite:///{{db_file}}'
os.environ['DATABASE_URL_SYNC'] = f'sqlite:///{{db_file}}'

from app.main import app as asgi_app
from a2wsgi import ASGIMiddleware

application = ASGIMiddleware(asgi_app)
"""
    wsgi_url = f"{base_url}/files/path/var/www/{username}_pythonanywhere_com_wsgi.py"
    r_wsgi = client.post(wsgi_url, files={"content": (f"{username}_pythonanywhere_com_wsgi.py", wsgi_content.encode("utf-8"), "text/plain")}, headers=headers)
    print("WSGI upload status:", r_wsgi.status_code)

    # 4. Recargar WebApp
    r_reload = client.post(f"{base_url}/webapps/{domain}/reload/", headers=headers)
    print("Reload Status:", r_reload.status_code)
