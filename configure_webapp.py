import httpx

token = "1e628583fd7c3b8aeadcf91b5ae29aa820f6daea"
username = "gthernandez96"
base_url = f"https://www.pythonanywhere.com/api/v0/user/{username}"
headers = {"Authorization": f"Token {token}"}
domain = "gthernandez96.pythonanywhere.com"

# 1. Configurar archivo WSGI
wsgi_content = """import sys
import os

username = 'gthernandez96'
project_home = f'/home/{username}/pos-empresarial'
backend_path = os.path.join(project_home, 'backend')

if backend_path not in sys.path:
    sys.path.insert(0, backend_path)
if project_home not in sys.path:
    sys.path.insert(0, project_home)

os.environ['ENVIRONMENT'] = 'production'
os.environ['DEBUG'] = 'false'
os.environ['SECRET_KEY'] = 'pos_empresa_prod_key_2026_gt_fel_jwt'
os.environ['DATABASE_URL'] = f'sqlite+aiosqlite:///{project_home}/pos_empresarial.db'

# Adaptador FastAPI (ASGI) a WSGI para PythonAnywhere
from app.main import app as asgi_app
from a2wsgi import ASGIMiddleware

application = ASGIMiddleware(asgi_app)
"""

with httpx.Client(verify=False, timeout=60.0) as client:
    # Subir WSGI
    wsgi_url = f"{base_url}/files/path/var/www/gthernandez96_pythonanywhere_com_wsgi.py"
    r = client.post(wsgi_url, files={"content": ("gthernandez96_pythonanywhere_com_wsgi.py", wsgi_content.encode("utf-8"), "text/plain")}, headers=headers)
    print("WSGI upload:", r.status_code)

    # Actualizar WebApp
    patch_url = f"{base_url}/webapps/{domain}/"
    patch_data = {
        "virtualenv_path": f"/home/{username}/.virtualenvs/pos-env",
        "source_directory": f"/home/{username}/pos-empresarial",
        "working_directory": f"/home/{username}/pos-empresarial"
    }
    r2 = client.patch(patch_url, json=patch_data, headers=headers)
    print("WebApp config update:", r2.status_code, r2.text)

    # Configurar Static Files
    static_url = f"{base_url}/webapps/{domain}/static_files/"
    static_data = {
        "url": "/static/",
        "path": f"/home/{username}/pos-empresarial/backend/app/static"
    }
    r3 = client.post(static_url, json=static_data, headers=headers)
    print("Static files config:", r3.status_code, r3.text)
