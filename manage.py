#!/usr/bin/env python
"""
Utilidad de línea de comandos para tareas administrativas de POS Empresarial.
Compatible con DigitalOcean App Platform (migrate, collectstatic, check).
"""
import os
import sys

def main():
    """Ejecutar tareas de gestión y despliegue."""
    project_root = os.path.dirname(os.path.abspath(__file__))
    backend_dir = os.path.join(project_root, "backend")
    if project_root not in sys.path:
        sys.path.insert(0, project_root)
    if backend_dir not in sys.path:
        sys.path.insert(0, backend_dir)

    os.environ.setdefault("DJANGO_SETTINGS_MODULE", "settings")

    # Si se ejecuta migrate, inicializar también tablas del POS Empresarial
    if len(sys.argv) > 1 and sys.argv[1] == "migrate":
        try:
            import asyncio
            from app.core.database import check_db_connection
            print("🚀 Sincronizando catálogo y tablas de base de datos POS...")
            asyncio.run(check_db_connection())
            print("✅ Tablas del POS sincronizadas correctamente.")
        except Exception as e:
            sys.stderr.write(f"⚠️ Aviso sincronizando tablas del POS: {e}\n")

    try:
        from django.core.management import execute_from_command_line
    except ImportError as exc:
        raise ImportError(
            "No se pudo importar Django. Asegúrate de que esté instalado en tu entorno virtual."
        ) from exc
    execute_from_command_line(sys.argv)

if __name__ == "__main__":
    main()
