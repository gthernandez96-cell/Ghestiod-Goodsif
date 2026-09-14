import os
import httpx

token = "1e628583fd7c3b8aeadcf91b5ae29aa820f6daea"
username = "gthernandez96"
base_url = f"https://www.pythonanywhere.com/api/v0/user/{username}"
headers = {"Authorization": f"Token {token}"}
domain = f"{username}.pythonanywhere.com"

files_to_sync = [
    "backend/app/templates/base.html",
    "backend/app/templates/inventory/index.html",
    "backend/app/templates/pos/index.html",
    "frontend/static/js/sw.js",
    "backend/app/templates/customers/index.html",
    "backend/app/templates/quotes/index.html",
    "backend/app/templates/transfers/index.html",
    "backend/app/templates/ap/index.html",
    "backend/app/templates/ar/index.html",
    "backend/app/templates/purchases/index.html",
    "backend/app/templates/purchases/form.html",
    "backend/app/templates/dashboard/index.html",
]

with httpx.Client(verify=False, timeout=60.0) as client:
    for rel_path in files_to_sync:
        local_path = os.path.join(os.path.dirname(__file__), rel_path)
        with open(local_path, "rb") as f:
            content = f.read()
        remote_url = f"{base_url}/files/path/home/{username}/pos-empresarial/{rel_path}"
        r = client.post(remote_url, files={"content": (os.path.basename(rel_path), content, "text/plain")}, headers=headers)
        print(f"Uploaded {rel_path}: {r.status_code}")

    # Reload webapp
    r_reload = client.post(f"{base_url}/webapps/{domain}/reload/", headers=headers)
    print(f"Reload webapp {domain}: {r_reload.status_code}")
