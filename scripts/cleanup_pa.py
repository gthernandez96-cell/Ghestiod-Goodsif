#!/usr/bin/env python3
"""
Script para limpiar directorios de dependencias residuales en PythonAnywhere
y luego instalar correctamente los paquetes con pip.
"""
import os
import sys
import time
import urllib.request
import urllib.parse
import json
import ssl

TOKEN = "1e628583fd7c3b8aeadcf91b5ae29aa820f6daea"
USERNAME = "gthernandez96"

def api_call(method, path, data=None, json_data=None):
    url = f"https://www.pythonanywhere.com/api/v0/user/{USERNAME}/{path}"
    headers = {"Authorization": f"Token {TOKEN}"}
    
    if json_data:
        body = json.dumps(json_data).encode()
        headers["Content-Type"] = "application/json"
    elif data:
        body = urllib.parse.urlencode(data).encode()
    else:
        body = None

    req = urllib.request.Request(url, data=body, headers=headers, method=method)
    ctx = ssl.create_default_context()
    ctx.check_hostname = False
    ctx.verify_mode = ssl.CERT_NONE
    
    try:
        with urllib.request.urlopen(req, context=ctx) as resp:
            return json.loads(resp.read())
    except Exception as e:
        print(f"⚠️ Error: {e}")
        return None

def send_console_commands(console_id, commands):
    url = f"https://www.pythonanywhere.com/api/v0/user/{USERNAME}/consoles/{console_id}/send_input/"
    headers = {
        "Authorization": f"Token {TOKEN}",
        "Content-Type": "application/json"
    }
    body = json.dumps({"input": commands + "\n"}).encode()
    req = urllib.request.Request(url, data=body, headers=headers, method="POST")
    ctx = ssl.create_default_context()
    ctx.check_hostname = False
    ctx.verify_mode = ssl.CERT_NONE
    try:
        with urllib.request.urlopen(req, context=ctx) as resp:
            return json.loads(resp.read())
    except Exception as e:
        print(f"⚠️ Error enviando comandos: {e}")
        return None

def get_console_output(console_id):
    url = f"https://www.pythonanywhere.com/api/v0/user/{USERNAME}/consoles/{console_id}/get_latest_output/"
    headers = {"Authorization": f"Token {TOKEN}"}
    req = urllib.request.Request(url, headers=headers, method="GET")
    ctx = ssl.create_default_context()
    ctx.check_hostname = False
    ctx.verify_mode = ssl.CERT_NONE
    try:
        with urllib.request.urlopen(req, context=ctx) as resp:
            data = json.loads(resp.read())
            return data.get("output", "")
    except Exception as e:
        print(f"⚠️ Error obteniendo output: {e}")
        return ""

if __name__ == "__main__":
    # Abrir consola bash en PythonAnywhere a través del browser
    print("ℹ️  Para completar el despliegue, necesitas ejecutar estos comandos en la consola Bash de PythonAnywhere:")
    print()
    print("1. Ve a https://www.pythonanywhere.com/user/gthernandez96/consoles/")
    print("2. Abre una nueva consola Bash")
    print("3. Ejecuta los siguientes comandos:")
    print()
    print("=" * 70)
    commands = """
# Limpiar directorios de dependencias locales incorrectas
rm -rf /home/gthernandez96/pos-empresarial/backend/fastapi \\
       /home/gthernandez96/pos-empresarial/backend/pydantic \\
       /home/gthernandez96/pos-empresarial/backend/pydantic_core \\
       /home/gthernandez96/pos-empresarial/backend/pydantic_settings \\
       /home/gthernandez96/pos-empresarial/backend/a2wsgi \\
       /home/gthernandez96/pos-empresarial/backend/starlette \\
       /home/gthernandez96/pos-empresarial/backend/annotated_types \\
       /home/gthernandez96/pos-empresarial/backend/typing_inspection \\
       /home/gthernandez96/pos-empresarial/backend/aiosqlite \\
       /home/gthernandez96/pos-empresarial/backend/dotenv

# Descomprimir el proyecto (si no está descomprimido)
cd /home/gthernandez96
unzip -o pos-empresarial.zip -d pos-empresarial 2>/dev/null || true

# Crear virtualenv e instalar dependencias
mkvirtualenv --python=/usr/bin/python3.10 pos-venv
pip install a2wsgi aiosqlite fastapi sqlalchemy jinja2 aiofiles pydantic pydantic-settings httpx python-dateutil pytz qrcode pillow python-jose passlib bcrypt python-multipart

echo "✅ Instalación completa"
"""
    print(commands)
    print("=" * 70)
    print()
    print("4. Después regresa aquí y dime que ya terminaste")
