# 🚀 Guía Completa de Despliegue en PythonAnywhere
## Sistema POS Empresarial con Facturación SAT (FEL)

Esta guía te explica paso a paso cómo subir y desplegar la aplicación en tu cuenta de **[PythonAnywhere](https://www.pythonanywhere.com)**.

---

### Paso 1: Clonar o Subir el Proyecto a PythonAnywhere

1. Inicia sesión en tu cuenta de **PythonAnywhere**.
2. Abre una consola Bash (`Open Bash Console`).
3. Clona tu repositorio de GitHub (o sube el proyecto comprimido):

```bash
cd ~
git clone https://github.com/TU_USUARIO/pos-empresarial.git
cd pos-empresarial
```

---

### Paso 2: Crear el Entorno Virtual (Virtualenv) e Instalar Dependencias

En la consola de PythonAnywhere, ejecuta los siguientes comandos para crear e instalar las librerías:

```bash
mkvirtualenv --python=python3.10 pos-venv
pip install --upgrade pip
pip install -r requirements.txt
```

*(Nota: PythonAnywhere incluye `python3.10` por defecto en cuentas gratuitas y de pago).*

---

### Paso 3: Configurar la Aplicación Web en la pestaña "Web"

1. Dirígete a la pestaña **Web** en la barra superior de PythonAnywhere.
2. Haz clic en **Add a new web app**.
3. Selecciona **Manual configuration** (no selecciones Django/Flask ni Bottle).
4. Elige **Python 3.10**.
5. Completa la configuración de rutas en la pestaña **Web**:

- **Source code**: `/home/TU_USUARIO/pos-empresarial`
- **Working directory**: `/home/TU_USUARIO/pos-empresarial`
- **Virtualenv**: `/home/TU_USUARIO/.virtualenvs/pos-venv`

---

### Paso 4: Configurar el Archivo de Entrada WSGI

En la pestaña **Web**, busca la sección **Code** y haz clic en el enlace del archivo WSGI configuration file (`/var/www/tu_usuario_pythonanywhere_com_wsgi.py`).

Borra todo el contenido existente y pega el siguiente código:

```python
import sys
import os

# 1. Definir ruta raíz del proyecto en PythonAnywhere
path = '/home/TU_USUARIO/pos-empresarial'
backend_path = os.path.join(path, 'backend')

if path not in sys.path:
    sys.path.insert(0, path)
if backend_path not in sys.path:
    sys.path.insert(0, backend_path)

# 2. Definir variables de entorno para producción
os.environ["DATABASE_URL"] = f"sqlite+aiosqlite:///{path}/pos.db"
os.environ["DATABASE_URL_SYNC"] = f"sqlite:///{path}/pos.db"
os.environ["SECRET_KEY"] = "clave-secreta-pos-empresarial-2026-prod"

# 3. Cargar la aplicación FastAPI adaptable a WSGI de PythonAnywhere
from a2wsgi import WSGIMiddleware
from app.main import app as fastapi_app

application = WSGIMiddleware(fastapi_app)
```

*(Reemplaza `TU_USUARIO` por tu nombre de usuario exacto en PythonAnywhere).*

---

### Paso 5: Recargar el Sitio Web

1. Vuelve a la pestaña **Web** de PythonAnywhere.
2. Haz clic en el botón verde grande **Reload tu_usuario.pythonanywhere.com**.
3. Ingresa a `https://tu_usuario.pythonanywhere.com` y ¡tu POS estará en producción! 🎉

---

### 📌 Mapeo de Archivos Estáticos (Static Files) en PythonAnywhere

En la sección **Static files** de la pestaña **Web**, puedes agregar la siguiente regla para servir archivos estáticos a máxima velocidad:

- **URL**: `/static/`
- **Directory**: `/home/TU_USUARIO/pos-empresarial/backend/app/static`
