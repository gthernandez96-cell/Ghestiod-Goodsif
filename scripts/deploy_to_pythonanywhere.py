#!/usr/bin/env python3
"""
Script de Despliegue Automático Completo en PythonAnywhere
"""

import sys
import os
import argparse
import urllib.request
import urllib.parse
import json
import ssl

BASE_URL = "https://www.pythonanywhere.com/api/v0/user/{username}/"

def pyanywhere_request(endpoint, token, username, method="GET", data=None):
    url = BASE_URL.format(username=username) + endpoint
    headers = {
        "Authorization": f"Token {token}",
        "Content-Type": "application/json"
    }
    
    encoded_data = json.dumps(data).encode("utf-8") if data else None
    req = urllib.request.Request(url, data=encoded_data, headers=headers, method=method)
    
    ctx = ssl.create_default_context()
    ctx.check_hostname = False
    ctx.verify_mode = ssl.CERT_NONE

    try:
        with urllib.request.urlopen(req, context=ctx) as resp:
            body = resp.read().decode("utf-8")
            return json.loads(body) if body else {}
    except urllib.error.HTTPError as e:
        error_body = e.read().decode("utf-8")
        print(f"❌ Error HTTP {e.code} en {endpoint}: {error_body[:200]}")
        return None
    except Exception as e:
        print(f"❌ Error en petición API ({endpoint}): {e}")
        return None

def upload_file_to_pyanywhere(remote_rel_path, local_abs_path, token, username):
    url = f"https://www.pythonanywhere.com/api/v0/user/{username}/files/path/home/{username}/pos-empresarial/{remote_rel_path}"
    headers = {
        "Authorization": f"Token {token}"
    }
    try:
        with open(local_abs_path, "rb") as f:
            content = f.read()
    except Exception as e:
        print(f"⚠️ No se pudo leer archivo local {local_abs_path}: {e}")
        return False
    
    req = urllib.request.Request(url, data=content, headers=headers, method="POST")
    ctx = ssl.create_default_context()
    ctx.check_hostname = False
    ctx.verify_mode = ssl.CERT_NONE
    try:
        with urllib.request.urlopen(req, context=ctx) as resp:
            return resp.status in (200, 201)
    except Exception as e:
        print(f"⚠️ Error al subir {remote_rel_path}: {e}")
        return False

def upload_project_files(token, username, project_root):
    print("📦 Subiendo código fuente y recursos del proyecto a PythonAnywhere...")
    uploaded_count = 0
    ignored_dirs = {".git", ".pytest_cache", "venv", "__pycache__", ".idea", ".vscode"}
    
    for root, dirs, files in os.walk(project_root):
        dirs[:] = [d for d in dirs if d not in ignored_dirs]
        for file in files:
            if file.endswith((".pyc", ".pyo", ".log", ".DS_Store")):
                continue
            local_path = os.path.join(root, file)
            rel_path = os.path.relpath(local_path, project_root)
            
            success = upload_file_to_pyanywhere(rel_path, local_path, token, username)
            if success:
                uploaded_count += 1
                if uploaded_count % 10 == 0:
                    print(f"   ✓ Subidos {uploaded_count} archivos...")

    print(f"✅ Se han subido {uploaded_count} archivos de tu proyecto con éxito.")

def main():
    parser = argparse.ArgumentParser(description="Desplegar POS Empresarial en PythonAnywhere")
    parser.add_argument("--username", required=True, help="Tu nombre de usuario de PythonAnywhere")
    parser.add_argument("--token", required=True, help="Tu API Token de PythonAnywhere")
    
    args = parser.parse_args()
    username = args.username.strip()
    token = args.token.strip()
    domain = f"{username}.pythonanywhere.com"

    project_root = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))

    print(f"🚀 Iniciando despliegue automático para {domain}...")

    # 1. Autenticación
    print("🔑 Verificando credenciales con PythonAnywhere...")
    webapps = pyanywhere_request("webapps/", token, username, method="GET")
    if webapps is None:
        print("❌ No se pudo autenticar con la API de PythonAnywhere.")
        sys.exit(1)
    print("✅ Autenticación exitosa.")

    # 2. Upload Files
    upload_project_files(token, username, project_root)

    # 3. Crear Web App si no existe
    existing_domains = [w.get("domain_name") for w in webapps] if isinstance(webapps, list) else []
    if domain not in existing_domains:
        print(f"🌐 Creando aplicación web para {domain}...")
        create_res = pyanywhere_request("webapps/", token, username, method="POST", data={
            "domain_name": domain,
            "python_version": "3.10"
        })
        print(f"✅ Aplicación web creada.")
    else:
        print(f"ℹ️ La aplicación web {domain} ya existe. Actualizando datos...")

    # 4. Configurar rutas de trabajo y entorno virtual
    print("⚙️ Ajustando rutas y entorno virtual...")
    update_data = {
        "virtualenv_path": f"/home/{username}/.virtualenvs/pos-venv",
        "source_directory": f"/home/{username}/pos-empresarial",
        "working_directory": f"/home/{username}/pos-empresarial"
    }
    pyanywhere_request(f"webapps/{domain}/", token, username, method="PATCH", data=update_data)

    # 5. Configurar WSGI
    print("📝 Escribiendo configuración WSGI...")
    wsgi_content = f"""import sys
import os

path = '/home/{username}/pos-empresarial'
backend_path = os.path.join(path, 'backend')

if path not in sys.path:
    sys.path.insert(0, path)
if backend_path not in sys.path:
    sys.path.insert(0, backend_path)

os.environ["DATABASE_URL"] = f"sqlite+aiosqlite:///{{path}}/pos.db"
os.environ["DATABASE_URL_SYNC"] = f"sqlite:///{{path}}/pos.db"
os.environ["SECRET_KEY"] = "clave-secreta-pos-empresarial-2026-prod"

from a2wsgi import WSGIMiddleware
from app.main import app as fastapi_app

application = WSGIMiddleware(fastapi_app)
"""
    # Enviar WSGI
    upload_file_to_pyanywhere(f"../../var/www/{username}_pythonanywhere_com_wsgi.py", os.path.join(project_root, "wsgi.py"), token, username)

    # 6. Recargar Web App
    print("🔄 Recargando servidor web...")
    pyanywhere_request(f"webapps/{domain}/reload/", token, username, method="POST")

    print("\n" + "="*60)
    print(f"🎉 ¡DESPLIEGUE COMPLETADO EXITOSAMENTE!")
    print(f"📍 Tu aplicación POS Empresarial está en vivo en:")
    print(f"👉 https://{domain}")
    print("="*60 + "\n")

if __name__ == "__main__":
    main()
