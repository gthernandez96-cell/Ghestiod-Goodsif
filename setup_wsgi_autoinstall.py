import os
import sys
import httpx

token = "1e628583fd7c3b8aeadcf91b5ae29aa820f6daea"
username = "gthernandez96"
base_url = f"https://www.pythonanywhere.com/api/v0/user/{username}"
headers = {"Authorization": f"Token {token}"}
domain = "gthernandez96.pythonanywhere.com"

wsgi_content = """import sys
import os
import zipfile
import subprocess

username = 'gthernandez96'
project_home = f'/home/{username}/pos-empresarial'
zip_path = f'/home/{username}/pos-empresarial.zip'

# 1. Descomprimir automáticamente el paquete
if os.path.exists(zip_path):
    os.makedirs(project_home, exist_ok=True)
    with zipfile.ZipFile(zip_path, 'r') as zip_ref:
        zip_ref.extractall(project_home)

# 2. Agregar paths
user_site = f'/home/{username}/.local/lib/python3.10/site-packages'
if user_site not in sys.path:
    sys.path.insert(0, user_site)

backend_path = os.path.join(project_home, 'backend')
if backend_path not in sys.path:
    sys.path.insert(0, backend_path)
if project_home not in sys.path:
    sys.path.insert(0, project_home)

# 3. Instalar dependencias si a2wsgi o fastapi faltan
try:
    import a2wsgi
    import fastapi
except ImportError:
    req_file = os.path.join(project_home, 'requirements.txt')
    if os.path.exists(req_file):
        subprocess.run([sys.executable, '-m', 'pip', 'install', '--user', '-r', req_file], check=False)
    import a2wsgi
    import fastapi

# 4. Configurar variables de entorno
os.environ['ENVIRONMENT'] = 'production'
os.environ['DEBUG'] = 'false'
os.environ['SECRET_KEY'] = 'pos_empresa_prod_key_2026_gt_fel_jwt'
os.environ['DATABASE_URL'] = f'sqlite+aiosqlite:///{project_home}/pos_empresarial.db'

# 5. Adaptar FastAPI (ASGI) a WSGI
from app.main import app as asgi_app
from a2wsgi import ASGIMiddleware

application = ASGIMiddleware(asgi_app)
"""

print("🚀 Configurando WSGI auto-instalable en PythonAnywhere...")
with httpx.Client(verify=False, timeout=60.0) as client:
    # 1. Subir WSGI
    wsgi_url = f"{base_url}/files/path/var/www/gthernandez96_pythonanywhere_com_wsgi.py"
    r = client.post(wsgi_url, files={"content": ("gthernandez96_pythonanywhere_com_wsgi.py", wsgi_content.encode("utf-8"), "text/plain")}, headers=headers)
    print("WSGI upload status:", r.status_code)

    # 2. Configurar directorios de WebApp sin forzar virtualenv inválido
    patch_url = f"{base_url}/webapps/{domain}/"
    patch_data = {
        "virtualenv_path": "",
        "source_directory": f"/home/{username}/pos-empresarial",
        "working_directory": f"/home/{username}/pos-empresarial"
    }
    r_patch = client.patch(patch_url, json=patch_data, headers=headers)
    print("WebApp config status:", r_patch.status_code, r_patch.text)

    # 3. Recargar la WebApp
    print("🔄 Recargando WebApp en PythonAnywhere...")
    r_reload = client.post(f"{base_url}/webapps/{domain}/reload/", headers=headers)
    print("Reload Status:", r_reload.status_code)
