import sys
import os
import uvicorn

# Añadir directorio backend al PYTHONPATH
project_root = os.path.dirname(os.path.abspath(__file__))
backend_dir = os.path.join(project_root, "backend")
if backend_dir not in sys.path:
    sys.path.insert(0, backend_dir)

if __name__ == "__main__":
    print("==================================================")
    print("🚀 Iniciando POS Empresarial...")
    print("📍 URL Sistema POS:  http://localhost:8000/pos")
    print("📍 Documentación API: http://localhost:8000/api/docs")
    print("==================================================")
    uvicorn.run("app.main:app", host="0.0.0.0", port=8000, reload=True)
