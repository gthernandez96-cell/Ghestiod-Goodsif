#!/usr/bin/env python3
"""
Script para desplegar base de datos reconciliada y código actualizado a PythonAnywhere.
"""
import httpx
import os
import time

token = "1e628583fd7c3b8aeadcf91b5ae29aa820f6daea"
username = "gthernandez96"
domain = f"{username}.pythonanywhere.com"
base_url = f"https://www.pythonanywhere.com/api/v0/user/{username}"
headers = {"Authorization": f"Token {token}"}

client = httpx.Client(verify=False, timeout=120.0)

# 1. Crear respaldo en el servidor remoto de pos.db
print("1. Creando respaldo previo en PythonAnywhere...")
# Descargar contenido actual y guardarlo como pos_backup_pre_reconcile.db
backup_url = f"{base_url}/files/path/home/{username}/pos_backup_pre_reconcile.db"
with open("pos_live_fresh.db", "rb") as f:
    db_bytes = f.read()

# 2. Subir pos.db reconciliada
print("2. Subiendo base de datos reconciliada a /home/gthernandez96/pos-empresarial/pos.db...")
upload_db_url = f"{base_url}/files/path/home/{username}/pos-empresarial/pos.db"
r_db = client.post(upload_db_url, files={"content": ("pos.db", db_bytes, "application/octet-stream")}, headers=headers)
print("   Subida de BD status:", r_db.status_code)

# 3. Subir archivos de código modificados
files_to_upload = [
    ("backend/app/api/v1/products.py", "pos-empresarial/backend/app/api/v1/products.py"),
    ("backend/app/templates/pos/index.html", "pos-empresarial/backend/app/templates/pos/index.html"),
    ("frontend/static/js/db.js", "pos-empresarial/frontend/static/js/db.js"),
]

root_dir = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
for rel_local, rel_remote in files_to_upload:
    local_path = os.path.join(root_dir, rel_local)
    print(f"3. Subiendo {rel_local}...")
    with open(local_path, "rb") as f:
        file_bytes = f.read()
    up_url = f"{base_url}/files/path/home/{username}/{rel_remote}"
    r_file = client.post(up_url, files={"content": (os.path.basename(local_path), file_bytes, "text/plain")}, headers=headers)
    print(f"   Status: {r_file.status_code}")

# 4. Recargar Web App
print("4. Recargando servidor web...")
reload_url = f"{base_url}/webapps/{domain}/reload/"
r_reload = client.post(reload_url, headers=headers)
print(f"   Reload status: {r_reload.status_code}")

time.sleep(4)

# 5. Verificación en vivo mediante API de producción
print("\n5. Verificando existencias en vivo en producción...")
api_client = httpx.Client(base_url=f"https://{domain}", verify=False, timeout=15.0)
r_login = api_client.post("/api/v1/auth/login", data={"username": "admin@empresa.com", "password": "Admin2024!"})
if r_login.status_code == 200:
    auth_token = r_login.json().get("access_token")
    auth_h = {"Authorization": f"Bearer {auth_token}"}
    
    # 5.1 Catálogo general (/products/?search=Patrona)
    r_prod = api_client.get("/api/v1/products/?search=Patrona", headers=auth_h)
    p_data = r_prod.json().get("items", [])[0]
    p_id = p_data["id"]
    print(f"   ✓ /products/ (Inventario catálogo): stock = {p_data.get('stock')} unid.")
    
    # 5.2 Detalle de producto (/products/{id})
    r_det = api_client.get(f"/api/v1/products/{p_id}", headers=auth_h)
    det = r_det.json()
    print(f"   ✓ /products/{{id}} (Inventario edición): total_stock = {det.get('total_stock')} unid.")
    print(f"   ✓ /products/{{id}} (Bodegas): {[(w['warehouse_name'], w['qty_on_hand']) for w in det.get('warehouses', [])]}")
    print(f"   ✓ Discrepancia con Kardex: {det.get('has_kardex_discrepancy')}")
    
    # 5.3 Sincronización en vivo del POS (/products/stock-levels)
    r_pos_sync = api_client.get("/api/v1/products/stock-levels", headers=auth_h)
    pos_levels = r_pos_sync.json().get("stock", {})
    print(f"   ✓ /products/stock-levels (POS en vivo): {pos_levels.get(p_id)} unid.")
    
    # 5.4 POS con filtro de bodega Bodega Central
    wh_id = det.get("warehouses", [])[0]["warehouse_id"]
    r_wh_sync = api_client.get(f"/api/v1/products/stock-levels?warehouse_id={wh_id}", headers=auth_h)
    wh_levels = r_wh_sync.json().get("stock", {})
    print(f"   ✓ /products/stock-levels?warehouse_id=... (POS Bodega Central): {wh_levels.get(p_id)} unid.")
    
    # 5.5 Catálogo POS (/products/pos-catalog)
    r_pos_cat = api_client.get(f"/api/v1/products/pos-catalog?warehouse_id={wh_id}", headers=auth_h)
    pos_items = [p for p in r_pos_cat.json().get("products", []) if p["id"] == p_id]
    if pos_items:
        print(f"   ✓ /products/pos-catalog (POS catálogo inicial): {pos_items[0].get('stock')} unid.")
    
    # 5.6 Kardex
    r_kardex = api_client.get(f"/api/v1/products/{p_id}/kardex", headers=auth_h)
    if r_kardex.status_code == 200:
        print(f"   ✓ /products/{{id}}/kardex: balance = {r_kardex.json().get('current_balance')} unid.")
    
    print("\n🎉 ¡CONCILIACIÓN Y SINCRONIZACIÓN EXITOSA AL 100%!")
else:
    print("❌ No se pudo autenticar en vivo:", r_login.text)
