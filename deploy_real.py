import httpx
import os

token = "1e628583fd7c3b8aeadcf91b5ae29aa820f6daea"
username = "gthernandez96"
base_url = f"https://www.pythonanywhere.com/api/v0/user/{username}"
headers = {"Authorization": f"Token {token}"}
domain = f"{username}.pythonanywhere.com"

# 1. Leer archivo local backend/app/templates/pos/index.html
local_pos_html = os.path.abspath("backend/app/templates/pos/index.html")
with open(local_pos_html, "rb") as f:
    pos_html_content = f.read()

print(f"📄 Archivo local pos/index.html: {len(pos_html_content)} bytes")

# 2. Contenido del nuevo WSGI con descompresión automática segura
new_wsgi = """import sys
import os
import zipfile

username = 'gthernandez96'
project_home = f'/home/{username}/pos-empresarial'
zip_path = f'/home/{username}/pos-empresarial.zip'

# Descomprimir si hay un ZIP nuevo subido
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

backend_path = os.path.join(project_home, 'backend')
user_site = f'/home/{username}/.local/lib/python3.10/site-packages'

for p in [user_site, backend_path, project_home]:
    if p not in sys.path:
        sys.path.insert(0, p)

os.chdir(project_home)

db_path = os.path.join(project_home, 'pos.db')
os.environ['ENVIRONMENT'] = 'production'
os.environ['DEBUG'] = 'false'
os.environ['SECRET_KEY'] = 'pos_empresa_prod_key_2026_gt_fel_jwt'
os.environ['DATABASE_URL'] = f'sqlite+aiosqlite:///{db_path}'
os.environ['DATABASE_URL_SYNC'] = f'sqlite:///{db_path}'
os.environ['PYTHONANYWHERE_SITE'] = 'true'

os.environ['http_proxy'] = 'http://proxy.server:3128'
os.environ['https_proxy'] = 'http://proxy.server:3128'

# Migración SQLite síncrona segura para Pre-venta
import sqlite3
try:
    if os.path.exists(db_path):
        conn = sqlite3.connect(db_path)
        cur = conn.cursor()
        cols = [r[1] for r in cur.execute("PRAGMA table_info(sale_orders)").fetchall()]
        if 'approved_by_id' not in cols:
            cur.execute("ALTER TABLE sale_orders ADD COLUMN approved_by_id VARCHAR(36) REFERENCES users(id);")
        if 'approved_at' not in cols:
            cur.execute("ALTER TABLE sale_orders ADD COLUMN approved_at TIMESTAMP;")
        if 'rejection_reason' not in cols:
            cur.execute("ALTER TABLE sale_orders ADD COLUMN rejection_reason TEXT;")
        if 'warehouse_verified' not in cols:
            cur.execute("ALTER TABLE sale_orders ADD COLUMN warehouse_verified BOOLEAN DEFAULT 0;")
        
        # Rol preventista
        cur.execute("SELECT id FROM roles WHERE code = 'preventista'")
        if not cur.fetchone():
            import uuid
            cur.execute(
                "INSERT INTO roles (id, name, code, description, is_system, is_deleted) VALUES (?, ?, ?, ?, ?, 0)",
                (uuid.uuid4().hex, 'Preventista', 'preventista', 'Acceso exclusivo a toma de pedidos y cotizaciones en Pre-Venta', 1)
            )
        conn.commit()
        conn.close()
except Exception as e:
    sys.stderr.write(f"Pre-sale DB migration error: {e}\\n")

from a2wsgi import ASGIMiddleware
from app.main import app as asgi_app

application = ASGIMiddleware(asgi_app)
"""

with httpx.Client(verify=False, timeout=60.0) as client:
    # A. Subir pos/index.html directamente
    print("🚀 Subiendo backend/app/templates/pos/index.html a PythonAnywhere...")
    target_url = f"{base_url}/files/path/home/{username}/pos-empresarial/backend/app/templates/pos/index.html"
    r_pos = client.post(target_url, files={"content": ("index.html", pos_html_content, "text/html")}, headers=headers)
    print("Status subida pos/index.html:", r_pos.status_code)

    # B. Subir el WSGI actualizado con auto-unzip
    print("⚙️ Actualizando WSGI con auto-unzip en PythonAnywhere...")
    wsgi_url = f"{base_url}/files/path/var/www/gthernandez96_pythonanywhere_com_wsgi.py"
    r_wsgi = client.post(wsgi_url, files={"content": ("gthernandez96_pythonanywhere_com_wsgi.py", new_wsgi.encode('utf-8'), "text/plain")}, headers=headers)
    print("Status WSGI:", r_wsgi.status_code)

    # C. Recargar WebApp
    print("🔄 Recargando WebApp...")
    r_reload = client.post(f"{base_url}/webapps/{domain}/reload/", headers=headers)
    print("Status Reload:", r_reload.status_code)

    # D. Validar que el archivo remoto ahora tiene el nuevo contenido
    print("🔍 Verificando archivo remoto...")
    r_verify = client.get(target_url, headers=headers)
    if "Product Card Grid (Matching Image 2 specifications)" in r_verify.text:
        print("✅ CONFIRMADO: El archivo remoto en PythonAnywhere tiene el nuevo CSS de la Imagen 2!")
    else:
        print("⚠️ Advertencia: No se encontró el texto esperado en el archivo remoto.")
