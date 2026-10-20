import httpx
import os
import sys

token = "1e628583fd7c3b8aeadcf91b5ae29aa820f6daea"
username = "gthernandez96"
base_url = f"https://www.pythonanywhere.com/api/v0/user/{username}"
headers = {"Authorization": f"Token {token}"}
domain = f"{username}.pythonanywhere.com"

rel_path = "backend/app/templates/ar/index.html"
abs_path = os.path.abspath(rel_path)

print(f"=== SUBIENDO {rel_path} A PYTHONANYWHERE ===")
with open(abs_path, "rb") as f:
    content = f.read()

filename = os.path.basename(rel_path)
target_url = f"{base_url}/files/path/home/{username}/pos-empresarial/{rel_path}"

with httpx.Client(verify=False, timeout=60.0) as client:
    r = client.post(target_url, files={"content": (filename, content, "text/html")}, headers=headers)
    print(f"Status upload: {r.status_code}")
    if r.status_code in (200, 201):
        print("✅ Archivo subido con éxito.")
    else:
        print(f"❌ Error al subir: {r.text}")
        sys.exit(1)

    print("\n🔄 Recargando WebApp...")
    reload_res = client.post(f"{base_url}/webapps/{domain}/reload/", headers=headers)
    print(f"Reload status: {reload_res.status_code}")
    if reload_res.status_code == 200:
        print("✅ WebApp recargada con éxito.")
    else:
        print(f"⚠️ Advertencia reload: {reload_res.text}")

print("=== FINALIZADO CON ÉXITO ===")
