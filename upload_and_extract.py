import os
import sys
import zipfile
import tempfile
import httpx

token = "1e628583fd7c3b8aeadcf91b5ae29aa820f6daea"
username = "gthernandez96"
base_url = f"https://www.pythonanywhere.com/api/v0/user/{username}"
headers = {"Authorization": f"Token {token}"}

# 1. Empaquetar el proyecto
root_dir = os.path.dirname(os.path.abspath(__file__))
temp_zip = tempfile.NamedTemporaryFile(suffix=".zip", delete=False)
temp_zip.close()

exclude_dirs = {'.git', '.pytest_cache', '__pycache__', 'venv', '.env', '.system_generated', 'scratch'}
exclude_exts = {'.pyc', '.pyo', '.db-journal'}

print("📦 Empaquetando archivos locales...")
with zipfile.ZipFile(temp_zip.name, 'w', zipfile.ZIP_DEFLATED) as z:
    for root, dirs, files in os.walk(root_dir):
        dirs[:] = [d for d in dirs if d not in exclude_dirs]
        for file in files:
            if any(file.endswith(ext) for ext in exclude_exts):
                continue
            full_path = os.path.join(root, file)
            rel_path = os.path.relpath(full_path, root_dir)
            z.write(full_path, arcname=rel_path)

print(f"📦 Tamaño del archivo ZIP: {os.path.getsize(temp_zip.name)} bytes")

# 2. Subir el ZIP a PythonAnywhere
print("☁️ Subiendo pos-empresarial.zip a PythonAnywhere...")
with open(temp_zip.name, 'rb') as f:
    zip_bytes = f.read()

with httpx.Client(verify=False, timeout=120.0) as client:
    upload_url = f"{base_url}/files/path/home/{username}/pos-empresarial.zip"
    resp = client.post(upload_url, files={"content": ("pos-empresarial.zip", zip_bytes, "application/zip")}, headers=headers)
    print("Upload Status:", resp.status_code)

os.remove(temp_zip.name)
