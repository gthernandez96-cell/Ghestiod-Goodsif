# 🚀 Guía de Despliegue en PythonAnywhere — POS Empresarial

Sigue estos sencillos pasos para tener tu aplicación funcionando en vivo en **`https://tu_usuario.pythonanywhere.com`**.

---

## 1. Crear Cuenta o Iniciar Sesión en PythonAnywhere
- Ingresa a [https://www.pythonanywhere.com/](https://www.pythonanywhere.com/) e inicia sesión.

---

## 2. Abrir una Consola Bash y Clonar / Subir el Proyecto
1. Ve a la pestaña **Consoles** y haz clic en **Bash**.
2. En la consola, clona tu repositorio de GitHub (o sube los archivos desde la pestaña *Files*):
   ```bash
   git clone <URL_DE_TU_REPOSITORIO_GITHUB> pos-empresarial
   cd pos-empresarial
   ```

---

## 3. Crear el Entorno Virtual e Instalar Dependencias
Dentro de la consola Bash:
```bash
# Crear entorno virtual con Python 3.10 o 3.11
mkvirtualenv pos-env --python=python3.10

# Instalar dependencias
pip install -r requirements.txt
```

---

## 4. Configurar la Aplicación Web en la Pestaña "Web"
1. Ve a la pestaña **Web** en el menú superior de PythonAnywhere.
2. Haz clic en **Add a new web app**.
3. Elige tu dominio (ej: `tu_usuario.pythonanywhere.com`) y presiona **Next**.
4. Selecciona **Manual configuration** (NO elijas Django ni Flask).
5. Selecciona **Python 3.10** (o 3.11).

---

## 5. Configurar Rutas y Entorno Virtual
En la página de configuración de la pestaña **Web**:

- **Virtualenv**:
  - En la sección *Virtualenv*, ingresa la ruta:
    ```
    /home/tu_usuario/.virtualenvs/pos-env
    ```
    *(Reemplaza `tu_usuario` con tu nombre de usuario de PythonAnywhere).*

- **Code / Working directory**:
  - `Source code`: `/home/tu_usuario/pos-empresarial`
  - `Working directory`: `/home/tu_usuario/pos-empresarial`

- **Static Files (Archivos Estáticos)**:
  - En la tabla de *Static files*, añade una fila:
    - **URL**: `/static/`
    - **Directory**: `/home/tu_usuario/pos-empresarial/backend/app/static`

---

## 6. Configurar el Archivo WSGI
1. En la misma pestaña **Web**, en la sección **Code**, haz clic en el enlace azul del archivo **WSGI configuration file** (`/var/www/tu_usuario_pythonanywhere_com_wsgi.py`).
2. Borra todo su contenido y pega lo siguiente (reemplazando `tu_usuario` por tu nombre de usuario real):

```python
import sys
import os

# Nombre de usuario en PythonAnywhere
username = 'tu_usuario'

# Rutas del proyecto
project_home = f'/home/{username}/pos-empresarial'
backend_path = os.path.join(project_home, 'backend')

if backend_path not in sys.path:
    sys.path.insert(0, backend_path)
if project_home not in sys.path:
    sys.path.insert(0, project_home)

# Variables de entorno
os.environ['ENVIRONMENT'] = 'production'
os.environ['DEBUG'] = 'false'
os.environ['SECRET_KEY'] = 'pos_empresa_prod_key_2026_gt_fel_jwt'
os.environ['DATABASE_URL'] = f'sqlite+aiosqlite:///{project_home}/pos_empresarial.db'

# Adaptar FastAPI (ASGI) a WSGI para PythonAnywhere
from app.main import app as asgi_app
from a2wsgi import ASGIMiddleware

application = ASGIMiddleware(asgi_app)
```

3. Haz clic en el botón verde **Save** (arriba a la derecha).

---

## 7. Recargar y Probar el Sitio
1. Regresa a la pestaña **Web**.
2. Haz clic en el botón verde grande **Reload tu_usuario.pythonanywhere.com**.
3. ¡Listo! Abre [https://tu_usuario.pythonanywhere.com/login](https://tu_usuario.pythonanywhere.com/login) para ingresar a tu POS Empresarial en la nube.
