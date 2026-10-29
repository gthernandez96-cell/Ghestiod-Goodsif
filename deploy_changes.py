import os
import sys
import httpx
import hashlib

TOKEN = "1e628583fd7c3b8aeadcf91b5ae29aa820f6daea"
USERNAME = "gthernandez96"
BASE_URL = f"https://www.pythonanywhere.com/api/v0/user/{USERNAME}"
HEADERS = {"Authorization": f"Token {TOKEN}"}
DOMAIN = f"{USERNAME}.pythonanywhere.com"

FILES_TO_DEPLOY = [
    # Backend APIs
    "backend/app/api/v1/ar.py",
    "backend/app/api/v1/auth.py",
    "backend/app/api/v1/branches.py",
    "backend/app/api/v1/cashier.py",
    "backend/app/api/v1/dashboard.py",
    "backend/app/api/v1/inventory.py",
    "backend/app/api/v1/partners.py",
    "backend/app/api/v1/presale.py",
    "backend/app/api/v1/products.py",
    "backend/app/api/v1/quotes.py",
    "backend/app/api/v1/reports.py",
    "backend/app/api/v1/sales.py",
    "backend/app/api/v1/users.py",
    "backend/app/services/fel_service.py",
    "backend/app/api/pages.py",
    "backend/app/models/sale.py",
    "backend/app/core/security.py",
    
    # Templates
    "backend/app/templates/ap/index.html",
    "backend/app/templates/ar/index.html",
    "backend/app/templates/auth/login.html",
    "backend/app/templates/base.html",
    "backend/app/templates/customers/form.html",
    "backend/app/templates/customers/index.html",
    "backend/app/templates/dashboard/index.html",
    "backend/app/templates/inventory/form.html",
    "backend/app/templates/inventory/index.html",
    "backend/app/templates/pos/index.html",
    "backend/app/templates/pos/presale.html",
    "backend/app/templates/pos/sessions.html",
    "backend/app/templates/purchases/form.html",
    "backend/app/templates/purchases/index.html",
    "backend/app/templates/quotes/form.html",
    "backend/app/templates/quotes/index.html",
    "backend/app/templates/reports/index.html",
    "backend/app/templates/settings/index.html",
    "backend/app/templates/transfers/index.html",
    "backend/app/templates/users/index.html",
    "backend/app/templates/website/admin.html",
    
    # Frontend CSS
    "frontend/static/css/main.css",
]

# STRICT SAFETY CHECK: Never touch database or env
FORBIDDEN_KEYWORDS = [".db", ".sqlite", ".env", "pos.db"]
for fpath in FILES_TO_DEPLOY:
    for forbidden in FORBIDDEN_KEYWORDS:
        if forbidden in fpath:
            raise RuntimeError(f"CRITICAL SAFETY VIOLATION: {fpath} contains forbidden keyword '{forbidden}'. Deployment aborted.")

def main():
    root_dir = os.path.dirname(os.path.abspath(__file__))
    print(f"🚀 Iniciando despliegue de {len(FILES_TO_DEPLOY)} archivos modificados a PythonAnywhere...")
    print(f"🛡️ Garantía de seguridad: Base de datos (pos.db / sqlite) NO será modificada.")

    success_count = 0
    with httpx.Client(verify=False, timeout=60.0) as client:
        for i, rel_path in enumerate(FILES_TO_DEPLOY, 1):
            local_path = os.path.join(root_dir, rel_path)
            if not os.path.exists(local_path):
                print(f"❌ Error: Archivo local no encontrado: {local_path}")
                sys.exit(1)

            with open(local_path, "rb") as f:
                content = f.read()

            remote_url = f"{BASE_URL}/files/path/home/{USERNAME}/pos-empresarial/{rel_path}"
            fname = os.path.basename(rel_path)
            
            # Subir archivo con manejo de throttling (HTTP 429)
            import time
            resp = None
            for attempt in range(5):
                resp = client.post(
                    remote_url,
                    files={"content": (fname, content, "application/octet-stream")},
                    headers=HEADERS
                )
                if resp.status_code == 429:
                    print(f"    ⏳ Esperando 12s por límite de tasa de la API...")
                    time.sleep(12)
                    continue
                break

            if resp and resp.status_code in (200, 201):
                print(f"  [{i}/{len(FILES_TO_DEPLOY)}] ✅ {rel_path} ({len(content)} bytes) - HTTP {resp.status_code}")
                success_count += 1
            else:
                status_code = resp.status_code if resp else "None"
                text = resp.text if resp else "No response"
                print(f"  [{i}/{len(FILES_TO_DEPLOY)}] ❌ {rel_path} - ERROR HTTP {status_code}: {text}")
                sys.exit(1)

        print(f"\n✨ {success_count} archivos sincronizados con éxito.")

        # Recargar Webapp en PythonAnywhere
        print(f"\n🔄 Recargando aplicación web en {DOMAIN}...")
        reload_url = f"{BASE_URL}/webapps/{DOMAIN}/reload/"
        r_reload = client.post(reload_url, headers=HEADERS)
        if r_reload.status_code == 200:
            print(f"✅ Webapp recargada exitosamente (HTTP 200).")
        else:
            print(f"⚠️ Advertencia en recarga de webapp: HTTP {r_reload.status_code}: {r_reload.text}")

        # Verificación de salud del sitio en vivo
        print(f"\n🌐 Verificando disponibilidad del sitio en https://{DOMAIN}/login...")
        try:
            r_live = client.get(f"https://{DOMAIN}/login", timeout=20.0, follow_redirects=True)
            print(f"  Status: HTTP {r_live.status_code}")
            if r_live.status_code == 200:
                print("  ✅ Sitio web respondiendo con éxito (HTTP 200 OK)!")
            else:
                print(f"  ⚠️ Respuesta inesperada: HTTP {r_live.status_code}")
        except Exception as e:
            print(f"  ⚠️ Error conectando al sitio web: {e}")

if __name__ == "__main__":
    main()
