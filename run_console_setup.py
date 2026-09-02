import httpx
import time

token = "1e628583fd7c3b8aeadcf91b5ae29aa820f6daea"
username = "gthernandez96"
base_url = f"https://www.pythonanywhere.com/api/v0/user/{username}"
headers = {"Authorization": f"Token {token}"}
domain = "gthernandez96.pythonanywhere.com"

with httpx.Client(verify=False, timeout=60.0) as client:
    # 1. Obtener la consola activa recién creada
    r_list = client.get(f"{base_url}/consoles/", headers=headers)
    consoles = r_list.json()
    console_id = consoles[0]["id"]
    print(f"🖥️ Usando consola limpia: {console_id}")

    # 2. Enviar comandos para descomprimir e instalar dependencias
    commands = (
        "mkdir -p /home/gthernandez96/pos-empresarial && "
        "unzip -o /home/gthernandez96/pos-empresarial.zip -d /home/gthernandez96/pos-empresarial && "
        "mkvirtualenv pos-env --python=python3.10 && "
        "pip install -r /home/gthernandez96/pos-empresarial/requirements.txt && "
        "echo '=== SETUP COMPLETED SUCCESSFULLY ==='\n"
    )
    
    print("⚙️ Ejecutando descompresión y creación del entorno virtual pos-env con pip...")
    r_send = client.post(f"{base_url}/consoles/{console_id}/send_input/", json={"input": commands}, headers=headers)
    print("Comandos enviados (Status:", r_send.status_code, ")")

    # 3. Monitorear progreso
    completed = False
    for i in range(60):
        time.sleep(5)
        r_out = client.get(f"{base_url}/consoles/{console_id}/get_latest_output/", headers=headers)
        output = r_out.json().get("output", "")
        if "=== SETUP COMPLETED SUCCESSFULLY ===" in output:
            print("🎉 ¡Instalación y entorno virtual completados con éxito!")
            completed = True
            break
        elif "Successfully installed" in output or "Collecting" in output or "Requirement already satisfied" in output:
            print(f"⏳ Instalando paquetes... ({(i+1)*5}s)")
        else:
            print(f"⏳ Procesando en PythonAnywhere... ({(i+1)*5}s)")

    # 4. Enlazar virtualenv a la WebApp
    print("🔗 Vinculando virtualenv /home/gthernandez96/.virtualenvs/pos-env a la WebApp...")
    patch_url = f"{base_url}/webapps/{domain}/"
    patch_data = {
        "virtualenv_path": f"/home/{username}/.virtualenvs/pos-env",
        "source_directory": f"/home/{username}/pos-empresarial",
        "working_directory": f"/home/{username}/pos-empresarial"
    }
    r_patch = client.patch(patch_url, json=patch_data, headers=headers)
    print("WebApp vinculada:", r_patch.status_code, r_patch.text)

    # 5. Recargar la WebApp
    print("🔄 Recargando WebApp...")
    r_reload = client.post(f"{base_url}/webapps/{domain}/reload/", headers=headers)
    print("Reload Status:", r_reload.status_code)
    print(f"\n🌐 Tu aplicación está lista en: https://{domain}/login")
