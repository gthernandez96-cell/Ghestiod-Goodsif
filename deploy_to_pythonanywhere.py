#!/usr/bin/env python3
"""
Script automatizado para desplegar POS Empresarial en PythonAnywhere
utilizando su REST API oficial.
"""

import os
import sys
import json
import zipfile
import tempfile
import urllib.request
import urllib.error

def deploy(username: str, api_token: str, python_version: str = "3.10"):
    base_url = f"https://www.pythonanywhere.com/api/v0/user/{username}"
    headers = {
        "Authorization": f"Token {api_token}"
    }

    domain = f"{username}.pythonanywhere.com"
    project_home = f"/home/{username}/pos-empresarial"
    backend_path = f"{project_home}/backend"

    print(f"🚀 Iniciando despliegue automatizado en PythonAnywhere para: {domain}")

    # 1. Empaquetar proyecto en un archivo ZIP temporal
    print("📦 Empaquetando archivos del proyecto...")
    temp_zip = tempfile.NamedTemporaryFile(suffix=".zip", delete=False)
    temp_zip.close()

    root_dir = os.path.dirname(os.path.abspath(__file__))
    exclude_dirs = {'.git', '.pytest_cache', '__pycache__', 'venv', '.env', '.system_generated'}
    exclude_exts = {'.pyc', '.pyo', '.db-journal'}

    with zipfile.ZipFile(temp_zip.name, 'w', zipfile.ZIP_DEFLATED) as z:
        for root, dirs, files in os.walk(root_dir):
            dirs[:] = [d for d in dirs if d not in exclude_dirs]
            for file in files:
                if any(file.endswith(ext) for ext in exclude_exts):
                    continue
                full_path = os.path.join(root, file)
                rel_path = os.path.relpath(full_path, root_dir)
                z.write(full_path, arcname=rel_path)

    # 2. Subir el ZIP a PythonAnywhere
    print("☁️ Subiendo paquete a PythonAnywhere...")
    upload_url = f"{base_url}/files/path/home/{username}/pos-empresarial.zip"
    
    with open(temp_zip.name, 'rb') as f:
        data = f.read()
    
    req = urllib.request.Request(upload_url, data=data, headers=headers, method='POST')
    try:
        with urllib.request.urlopen(req) as resp:
            print(f"✅ Archivo ZIP subido con éxito (Status {resp.status})")
    except urllib.error.HTTPError as e:
        print(f"⚠️ Error al subir ZIP: {e.code} - {e.read().decode('utf-8')}")

    os.remove(temp_zip.name)

    # 3. Configurar el archivo WSGI
    wsgi_content = f"""import sys
import os

username = '{username}'
project_home = f'/home/{{username}}/pos-empresarial'
backend_path = os.path.join(project_home, 'backend')

if backend_path not in sys.path:
    sys.path.insert(0, backend_path)
if project_home not in sys.path:
    sys.path.insert(0, project_home)

os.environ['ENVIRONMENT'] = 'production'
os.environ['DEBUG'] = 'false'
os.environ['SECRET_KEY'] = 'pos_empresa_prod_key_2026_gt_fel_jwt'
os.environ['DATABASE_URL'] = f'sqlite+aiosqlite:///{{project_home}}/pos_empresarial.db'

from app.main import app as asgi_app
from a2wsgi import ASGIMiddleware

application = ASGIMiddleware(asgi_app)
"""

    print("⚙️ Configurando archivo WSGI...")
    wsgi_file_path = f"/var/www/{username.replace('.', '_')}_pythonanywhere_com_wsgi.py"
    wsgi_url = f"{base_url}/files/path{wsgi_file_path}"
    
    req_wsgi = urllib.request.Request(
        wsgi_url, 
        data=wsgi_content.encode('utf-8'), 
        headers=headers, 
        method='POST'
    )
    try:
        with urllib.request.urlopen(req_wsgi) as resp:
            print(f"✅ Archivo WSGI configurado correctamente")
    except urllib.error.HTTPError as e:
        print(f"ℹ️ WSGI: {e.code} - {e.read().decode('utf-8')}")

    # 4. Verificar o crear la Web App
    print(f"🌐 Verificando Web App {domain}...")
    webapp_url = f"{base_url}/webapps/{domain}/"
    req_get_web = urllib.request.Request(webapp_url, headers=headers)
    
    webapp_exists = False
    try:
        with urllib.request.urlopen(req_get_web) as resp:
            webapp_exists = True
            print(f"✅ Web App encontrada: {domain}")
    except urllib.error.HTTPError as e:
        if e.code == 404:
            print(f"🆕 Creando nueva Web App {domain}...")
            create_payload = json.dumps({
                "domain_name": domain,
                "python_version": python_version
            }).encode('utf-8')
            create_headers = {**headers, "Content-Type": "application/json"}
            req_create = urllib.request.Request(
                f"{base_url}/webapps/", 
                data=create_payload, 
                headers=create_headers, 
                method='POST'
            )
            try:
                with urllib.request.urlopen(req_create) as resp:
                    print("✅ Web App creada exitosamente")
                    webapp_exists = True
            except Exception as ex:
                print(f"⚠️ Error creando Web App: {ex}")

    # 5. Configurar virtualenv y directorios de la Web App
    if webapp_exists:
        patch_payload = json.dumps({
            "virtualenv_path": f"/home/{username}/.virtualenvs/pos-env",
            "source_directory": project_home,
            "working_directory": project_home
        }).encode('utf-8')
        patch_headers = {**headers, "Content-Type": "application/json"}
        req_patch = urllib.request.Request(
            webapp_url,
            data=patch_payload,
            headers=patch_headers,
            method='PATCH'
        )
        try:
            with urllib.request.urlopen(req_patch) as resp:
                print("✅ Configuración de rutas y Virtualenv actualizada")
        except Exception as e:
            print(f"ℹ️ Config WebApp: {e}")

        # 6. Recargar Web App
        print("🔄 Recargando Web App...")
        reload_url = f"{base_url}/webapps/{domain}/reload/"
        req_reload = urllib.request.Request(reload_url, data=b"", headers=headers, method='POST')
        try:
            with urllib.request.urlopen(req_reload) as resp:
                print(f"🎉 ¡Despliegue completado con éxito!")
                print(f"🌐 Tu aplicación está disponible en: https://{domain}/login")
        except urllib.error.HTTPError as e:
            print(f"⚠️ Error al recargar: {e.code} - {e.read().decode('utf-8')}")

if __name__ == "__main__":
    if len(sys.argv) < 3:
        print("Uso: python3 deploy_to_pythonanywhere.py <usuario> <api_token>")
        sys.exit(1)
    deploy(sys.argv[1], sys.argv[2])
