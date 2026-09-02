#!/usr/bin/env python3
import os
import sys
import time
import urllib.request
import ssl

TOKEN = "1e628583fd7c3b8aeadcf91b5ae29aa820f6daea"
USERNAME = "gthernandez96"
PROJECT_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))

def upload_file(rel_path, abs_path):
    url = f"https://www.pythonanywhere.com/api/v0/user/{USERNAME}/files/path/home/{USERNAME}/pos-empresarial/{rel_path}"
    
    try:
        with open(abs_path, "rb") as f:
            content = f.read()
    except Exception as e:
        print(f"⚠️ Error leyendo {abs_path}: {e}")
        return False

    req = urllib.request.Request(
        url,
        data=content,
        headers={"Authorization": f"Token {TOKEN}"},
        method="POST"
    )
    
    ctx = ssl.create_default_context()
    ctx.check_hostname = False
    ctx.verify_mode = ssl.CERT_NONE

    try:
        with urllib.request.urlopen(req, context=ctx) as resp:
            if resp.status in (200, 201):
                return True
    except Exception as e:
        print(f"⚠️ Error ({resp.status if 'resp' in locals() else 'REQ'}): {e}")
        return False
    return True

def main():
    print("🚀 Subiendo archivos del proyecto POS a PythonAnywhere...")
    ignored_dirs = {".git", ".pytest_cache", "venv", "__pycache__", ".idea", ".vscode", "brain", ".tempmediaStorage"}
    
    success_count = 0
    total_count = 0

    for root, dirs, files in os.walk(PROJECT_ROOT):
        dirs[:] = [d for d in dirs if d not in ignored_dirs]
        for file in files:
            if file.endswith((".pyc", ".pyo", ".log", ".DS_Store", ".webp", ".png", ".zip")):
                continue
            abs_path = os.path.join(root, file)
            rel_path = os.path.relpath(abs_path, PROJECT_ROOT)
            
            total_count += 1
            ok = upload_file(rel_path, abs_path)
            if ok:
                success_count += 1
                print(f"  [{success_count}] ✓ Subido: {rel_path}")
            else:
                print(f"  [X] ❌ Falló: {rel_path}")
            
            time.sleep(0.35) # Pausa estratégica para evitar límite de frecuencia HTTP 429

    print(f"\n✅ Finalizado: {success_count}/{total_count} archivos subidos a /home/{USERNAME}/pos-empresarial/")

if __name__ == "__main__":
    main()
