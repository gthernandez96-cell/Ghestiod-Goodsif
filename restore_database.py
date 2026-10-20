import httpx
import os
import sys

token = "1e628583fd7c3b8aeadcf91b5ae29aa820f6daea"
username = "gthernandez96"
base_url = f"https://www.pythonanywhere.com/api/v0/user/{username}"
headers = {"Authorization": f"Token {token}"}
domain = f"{username}.pythonanywhere.com"

print("🔍 Verificando archivo de respaldo local...")
local_backup = "pos_prod_final_reconciled.db"
if not os.path.exists(local_backup):
    local_backup = "pos_prod_backup.db"

with open(local_backup, "rb") as f:
    db_bytes = f.read()

print(f"📦 Tamaño de la base de datos de producción a restaurar: {len(db_bytes)} bytes")

# 1. WSGI con restauración automática y protección
new_wsgi = """import sys
import os
import zipfile
import shutil

username = 'gthernandez96'
project_home = f'/home/{username}/pos-empresarial'
zip_path = f'/home/{username}/pos-empresarial.zip'

# 1. Descomprimir si hay un ZIP nuevo (sin tocar DBs)
if os.path.exists(zip_path):
    try:
        with zipfile.ZipFile(zip_path, 'r') as zf:
            zf.extractall(project_home)
        try:
            os.remove(zip_path)
        except Exception:
            pass
    except Exception as e:
        sys.stderr.write(f"Unzip error: {e}\\n")

# 2. Asegurar que pos.db use el respaldo de produccion si esta disponible
db_path = os.path.join(project_home, 'pos.db')
prod_backup = os.path.join(project_home, 'pos_prod_final_reconciled.db')
if os.path.exists(prod_backup):
    if not os.path.exists(db_path) or os.path.getsize(db_path) < 9000000:
        try:
            shutil.copyfile(prod_backup, db_path)
        except Exception as e:
            sys.stderr.write(f"DB restore error: {e}\\n")

backend_path = os.path.join(project_home, 'backend')
user_site = f'/home/{username}/.local/lib/python3.10/site-packages'

for p in [user_site, backend_path, project_home]:
    if p not in sys.path:
        sys.path.insert(0, p)

os.chdir(project_home)

os.environ['ENVIRONMENT'] = 'production'
os.environ['DEBUG'] = 'false'
os.environ['SECRET_KEY'] = 'pos_empresa_prod_key_2026_gt_fel_jwt'
os.environ['DATABASE_URL'] = f'sqlite+aiosqlite:///{db_path}'
os.environ['DATABASE_URL_SYNC'] = f'sqlite:///{db_path}'
os.environ['PYTHONANYWHERE_SITE'] = 'true'

os.environ['http_proxy'] = 'http://proxy.server:3128'
os.environ['https_proxy'] = 'http://proxy.server:3128'

from a2wsgi import ASGIMiddleware
from app.main import app as asgi_app

application = ASGIMiddleware(asgi_app)
"""

with httpx.Client(verify=False, timeout=120.0) as client:
    # A. Subir WSGI
    print("⚙️ Actualizando WSGI en PythonAnywhere...")
    wsgi_url = f"{base_url}/files/path/var/www/gthernandez96_pythonanywhere_com_wsgi.py"
    r_wsgi = client.post(wsgi_url, files={"content": ("gthernandez96_pythonanywhere_com_wsgi.py", new_wsgi.encode('utf-8'), "text/plain")}, headers=headers)
    print("Status WSGI:", r_wsgi.status_code)

    # B. Subir pos.db restaurada (9.1MB)
    print("📤 Restaurando pos.db en PythonAnywhere (9.1 MB)...")
    db_url = f"{base_url}/files/path/home/{username}/pos-empresarial/pos.db"
    r_db = client.post(db_url, files={"content": ("pos.db", db_bytes, "application/octet-stream")}, headers=headers)
    print("Status subida pos.db:", r_db.status_code)

    # C. Recargar WebApp
    print("🔄 Recargando WebApp...")
    r_reload = client.post(f"{base_url}/webapps/{domain}/reload/", headers=headers)
    print("Status Reload:", r_reload.status_code)

print("🎉 Base de datos de producción restaurada con éxito.")
