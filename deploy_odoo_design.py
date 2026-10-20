import os
import zipfile
import tempfile
import httpx

token = "1e628583fd7c3b8aeadcf91b5ae29aa820f6daea"
username = "gthernandez96"
base_url = f"https://www.pythonanywhere.com/api/v0/user/{username}"
headers = {"Authorization": f"Token {token}"}
domain = f"{username}.pythonanywhere.com"

root_dir = os.path.dirname(os.path.abspath(__file__))

print("🚀 1. Subiendo archivos clave modificados directamente...")
files_to_upload_direct = [
    "backend/app/templates/pos/presale.html",
    "backend/app/templates/pos/solicitudes.html",
    "backend/app/templates/reports/index.html",
    "backend/app/templates/pos/ticket_receipt.html",
    "backend/app/api/v1/reports.py",
    "backend/app/api/v1/cashier.py",
    "backend/app/api/pages.py",
    "backend/app/api/v1/presale.py",
    "backend/app/api/v1/products.py",
    "backend/app/api/v1/partners.py",
    "backend/tests/test_endpoints.py",
    "backend/tests/test_presale_workflow.py"
]

with httpx.Client(verify=False, timeout=120.0) as client:
    for rel_path in files_to_upload_direct:
        local_file = os.path.join(root_dir, rel_path)
        with open(local_file, "rb") as f:
            content = f.read()
        target_url = f"{base_url}/files/path/home/{username}/pos-empresarial/{rel_path}"
        filename = os.path.basename(rel_path)
        r = client.post(target_url, files={"content": (filename, content, "application/octet-stream")}, headers=headers)
        print(f"  -> Subido {rel_path} ({len(content)} bytes) - Status: {r.status_code}")

    # 2. Empaquetar proyecto completo (excluyendo .db y temporales)
    temp_zip = tempfile.NamedTemporaryFile(suffix=".zip", delete=False)
    temp_zip.close()
    
    exclude_dirs = {'.git', '.pytest_cache', '__pycache__', 'venv', '.env', '.system_generated', 'scratch', 'node_modules', '.tempmediaStorage', '.user_uploaded'}
    exclude_exts = {'.pyc', '.pyo', '.db-journal', '.zip', '.tar.gz', '.webp', '.db'}
    
    print("📦 2. Empaquetando ZIP completo...")
    with zipfile.ZipFile(temp_zip.name, 'w', zipfile.ZIP_DEFLATED) as z:
        for root, dirs, files in os.walk(root_dir):
            dirs[:] = [d for d in dirs if d not in exclude_dirs]
            for file in files:
                if any(file.endswith(ext) for ext in exclude_exts):
                    continue
                full_path = os.path.join(root, file)
                rel = os.path.relpath(full_path, root_dir)
                z.write(full_path, arcname=rel)

    print(f"📦 Tamaño del ZIP: {os.path.getsize(temp_zip.name)} bytes")

    # 3. Subir ZIP a PythonAnywhere
    print("☁️ 3. Subiendo ZIP a PythonAnywhere...")
    with open(temp_zip.name, 'rb') as f:
        zip_bytes = f.read()
    
    upload_url = f"{base_url}/files/path/home/{username}/pos-empresarial.zip"
    resp = client.post(upload_url, files={"content": ("pos-empresarial.zip", zip_bytes, "application/zip")}, headers=headers)
    print("  -> Upload ZIP Status:", resp.status_code)
    os.remove(temp_zip.name)

    # 4. Recargar Webapp
    print("🔄 4. Recargando Webapp...")
    r_reload = client.post(f"{base_url}/webapps/{domain}/reload/", headers=headers)
    print("  -> Reload Status:", r_reload.status_code)

    # 5. Verificar archivo remoto
    print("🔍 5. Verificando presale.html remoto...")
    verify_url = f"{base_url}/files/path/home/{username}/pos-empresarial/backend/app/templates/pos/presale.html"
    r_verify = client.get(verify_url, headers=headers)
    if "MODAL DE DESPACHO & LOGÍSTICA DE PRE-VENTA (ODOO ENTERPRISE)" in r_verify.text:
        print("✅ CONFIRMADO: presale.html en PythonAnywhere tiene el diseño Odoo Enterprise!")
    else:
        print("⚠️ Advertencia: No se encontró la cabecera Odoo Enterprise en el archivo remoto.")
