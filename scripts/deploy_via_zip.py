#!/usr/bin/env python3
"""
Script de Despliegue Robusto en PythonAnywhere utilizando Paquete ZIP + Consola API
"""

import sys
import os
import argparse
import urllib.request
import urllib.parse
import json
import ssl
import zipfile
import time

BASE_URL = "https://www.pythonanywhere.com/api/v0/user/{username}/"

def get_ssl_context():
    ctx = ssl.create_default_context()
    ctx.check_hostname = False
    ctx.verify_mode = ssl.CERT_NONE
    return ctx

def pyanywhere_request(endpoint, token, username, method="GET", data=None):
    url = BASE_URL.format(username=username) + endpoint
    headers = {
        "Authorization": f"Token {token}",
        "Content-Type": "application/json"
    }
    
    encoded_data = json.dumps(data).encode("utf-8") if data else None
    req = urllib.request.Request(url, data=encoded_data, headers=headers, method=method)
    
    try:
        with urllib.request.urlopen(req, context=get_ssl_context()) as resp:
            body = resp.read().decode("utf-8")
            return json.loads(body) if body else {}
    except urllib.error.HTTPError as e:
        error_body = e.read().decode("utf-8")
        print(f"❌ Error HTTP {e.code} en {endpoint}: {error_body[:200]}")
        return None
    except Exception as e:
        print(f"❌ Error en petición API ({endpoint}): {e}")
        return None

def create_zip_package(project_root, zip_path):
    print("📦 Comprimiendo el código fuente en pos-empresarial.zip...")
    ignored_dirs = {".git", ".pytest_cache", "venv", "__pycache__", ".idea", ".vscode"}
    
    with zipfile.ZipFile(zip_path, 'w', zipfile.ZIP_DEFLATED) as zipf:
        for root, dirs, files in os.walk(project_root):
            dirs[:] = [d for d in dirs if d not in ignored_dirs]
            for file in files:
                if file.endswith((".pyc", ".pyo", ".log", ".DS_Store", ".zip")):
                    continue
                local_path = os.path.join(root, file)
                rel_path = os.path.relpath(local_path, project_root)
                zipf.write(local_path, rel_path)
    
    print(f"✅ Paquete ZIP creado ({os.path.getsize(zip_path) / 1024 / 1024:.2f} MB).")

def upload_zip_to_pyanywhere(zip_path, token, username):
    print("🚀 Subiendo pos-empresarial.zip a PythonAnywhere...")
    url = f"https://www.pythonanywhere.com/api/v0/user/{username}/files/path/home/{username}/pos-empresarial.zip"
    headers = {
        "Authorization": f"Token {token}"
    }
    with open(zip_path, "rb") as f:
        content = f.read()
    
    req = urllib.request.Request(url, data=content, headers=headers, method="POST")
    try:
        with urllib.request.urlopen(req, context=get_ssl_context()) as resp:
            if resp.status in (200, 201):
                print("✅ Paquete ZIP subido exitosamente a PythonAnywhere.")
                return True
    except Exception as e:
        print(f"❌ Error al subir ZIP: {e}")
        return False
    return False

def upload_wsgi_file(token, username):
    print("📝 Configurando archivo WSGI...")
    url = f"https://www.pythonanywhere.com/api/v0/user/{username}/files/path/var/www/{username}_pythonanywhere_com_wsgi.py"
    headers = {
        "Authorization": f"Token {token}"
    }
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
    req = urllib.request.Request(url, data=wsgi_content.encode('utf-8'), headers=headers, method="POST")
    try:
        with urllib.request.urlopen(req, context=get_ssl_context()) as resp:
            print("✅ Archivo WSGI actualizado.")
            return True
    except Exception as e:
        print(f"⚠️ Error actualizando WSGI: {e}")
        return False

def execute_remote_bash_commands(token, username):
    print("⚡ Ejecutando descompresión y virtualenv en PythonAnywhere via Consoles API...")
    
    # 1. Crear consola bash
    console_res = pyanywhere_request("consoles/", token, username, method="POST", data={"executable": "bash"})
    if not console_res or "id" not in console_res:
        print("⚠️ No se pudo iniciar consola interactiva. Intentando recuperar consola activa...")
        consoles = pyanywhere_request("consoles/", token, username, method="GET")
        if isinstance(consoles, list) and len(consoles) > 0:
            console_id = consoles[0]["id"]
        else:
            print("❌ No se pudo obtener consola de comandos.")
            return False
    else:
        console_id = console_res["id"]
    
    print(f"🖥️ Consola de comandos #{console_id} iniciada.")

    # 2. Enviar secuencia de comandos bash
    cmds = (
        f"mkdir -p /home/{username}/pos-empresarial && "
        f"unzip -o /home/{username}/pos-empresarial.zip -d /home/{username}/pos-empresarial && "
        f"virtualenv --python=python3.10 /home/{username}/.virtualenvs/pos-venv && "
        f"/home/{username}/.virtualenvs/pos-venv/bin/pip install -r /home/{username}/pos-empresarial/requirements.txt\n"
    )
    
    input_url = f"consoles/{console_id}/send_input/"
    pyanywhere_request(input_url, token, username, method="POST", data={"input": cmds})
    
    print("⏳ Esperando que el servidor procese la instalación de dependencias en la consola (15 segundos)...")
    time.sleep(15)
    return True

def main():
    parser = argparse.ArgumentParser(description="Desplegar POS Empresarial en PythonAnywhere via ZIP")
    parser.add_argument("--username", required=True, help="Tu nombre de usuario de PythonAnywhere")
    parser.add_argument("--token", required=True, help="Tu API Token de PythonAnywhere")
    
    args = parser.parse_args()
    username = args.username.strip()
    token = args.token.strip()
    domain = f"{username}.pythonanywhere.com"

    project_root = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    zip_path = os.path.join(project_root, "pos-empresarial.zip")

    print(f"🚀 Iniciando despliegue automatizado para {domain}...")

    # 1. Crear ZIP local
    create_zip_package(project_root, zip_path)

    # 2. Subir ZIP en 1 sola llamada API
    if not upload_zip_to_pyanywhere(zip_path, token, username):
        sys.exit(1)

    # 3. Descomprimir e instalar dependencias en PythonAnywhere
    execute_remote_bash_commands(token, username)

    # 4. Configurar WSGI file
    upload_wsgi_file(token, username)

    # 5. Crear/Actualizar WebApp
    print("⚙️ Configurando Web App y Virtualenv en la pestaña Web...")
    webapps = pyanywhere_request("webapps/", token, username, method="GET")
    existing_domains = [w.get("domain_name") for w in webapps] if isinstance(webapps, list) else []
    
    if domain not in existing_domains:
        pyanywhere_request("webapps/", token, username, method="POST", data={
            "domain_name": domain,
            "python_version": "3.10"
        })
    
    pyanywhere_request(f"webapps/{domain}/", token, username, method="PATCH", data={
        "virtualenv_path": f"/home/{username}/.virtualenvs/pos-venv",
        "source_directory": f"/home/{username}/pos-empresarial",
        "working_directory": f"/home/{username}/pos-empresarial"
    })

    # 6. Reload Web App
    print("🔄 Recargando la aplicación web...")
    pyanywhere_request(f"webapps/{domain}/reload/", token, username, method="POST")

    print("\n" + "="*60)
    print(f"🎉 ¡DESPLIEGUE AUTOMATIZADO FINALIZADO!")
    print(f"📍 Tu aplicación POS Empresarial está disponible en:")
    print(f"👉 https://{domain}")
    print("="*60 + "\n")

if __name__ == "__main__":
    main()
