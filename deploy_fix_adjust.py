import os
import sys
import httpx

token = "1e628583fd7c3b8aeadcf91b5ae29aa820f6daea"
username = "gthernandez96"
base_url = f"https://www.pythonanywhere.com/api/v0/user/{username}"
headers = {"Authorization": f"Token {token}"}
domain = f"{username}.pythonanywhere.com"

files_to_upload = [
    "backend/app/api/v1/products.py",
    "backend/app/templates/inventory/form.html",
    "frontend/static/js/api.js",
]

print("=== INICIANDO DESPLIEGUE DE CORRECCIÓN DE AJUSTE DE STOCK ===")

with httpx.Client(verify=False, timeout=60.0) as client:
    for rel_path in files_to_upload:
        abs_path = os.path.abspath(rel_path)
        if not os.path.exists(abs_path):
            print(f"❌ Error: Archivo local no existe: {abs_path}")
            sys.exit(1)
            
        with open(abs_path, "rb") as f:
            content = f.read()
            
        filename = os.path.basename(rel_path)
        target_url = f"{base_url}/files/path/home/{username}/pos-empresarial/{rel_path}"
        mime = "application/javascript" if rel_path.endswith(".js") else ("text/html" if rel_path.endswith(".html") else "text/x-python")
        
        print(f"📤 Subiendo {rel_path} ({len(content)} bytes)...")
        r = client.post(target_url, files={"content": (filename, content, mime)}, headers=headers)
        if r.status_code in (200, 201):
            print(f"   ✅ OK [{r.status_code}]")
        else:
            print(f"   ❌ Falló subida [{r.status_code}]: {r.text}")
            sys.exit(1)

    print(f"\n🔄 Recargando WebApp {domain}...")
    reload_res = client.post(f"{base_url}/webapps/{domain}/reload/", headers=headers)
    print(f"Reload status: {reload_res.status_code}")
    if reload_res.status_code == 200:
        print("✅ WebApp recargada con éxito.")
    else:
        print(f"⚠️ Advertencia reload: {reload_res.text}")

print("=== DESPLIEGUE FINALIZADO CON ÉXITO ===")
