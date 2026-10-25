"""
Servicio de Copias de Seguridad Automáticas en la Nube (DigitalOcean Spaces).
Genera respaldos estructurados y los almacena en el bucket privado de Spaces con rotación de 30 días.
"""
import os
import json
import uuid
import asyncio
import logging
from datetime import datetime, date, timezone
from decimal import Decimal
from typing import Optional, Dict, Any, List
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.base import BaseModel
from app.services.spaces_storage import (
    is_spaces_configured,
    upload_to_spaces,
    list_spaces_files,
    delete_from_spaces
)

logger = logging.getLogger(__name__)


def serialize_val(val):
    """Serializa valores de base de datos a tipos JSON estándar."""
    if isinstance(val, (datetime, date)):
        return val.isoformat()
    if isinstance(val, Decimal):
        return float(val)
    if isinstance(val, uuid.UUID):
        return str(val)
    if isinstance(val, bytes):
        return val.hex()
    return val


async def dump_database_to_dict(session: AsyncSession, company_id: Optional[uuid.UUID] = None) -> Dict[str, Any]:
    """Genera un diccionario con el volcado completo de todas las tablas registradas en el sistema."""
    user_company_id = str(company_id) if company_id else None

    backup_data = {
        "metadata": {
            "version": "1.0",
            "system": "Ghestiod POS Empresarial",
            "created_at": datetime.now(timezone.utc).isoformat(),
            "company_id": user_company_id,
        },
        "tables": {}
    }

    for table in BaseModel.metadata.sorted_tables:
        try:
            has_company_col = "company_id" in table.c
            if has_company_col and user_company_id:
                stmt = text(f'SELECT * FROM "{table.name}" WHERE company_id = :comp_id')
                res = await session.execute(stmt, {"comp_id": user_company_id})
            elif table.name == "companies" and user_company_id:
                stmt = text(f'SELECT * FROM "{table.name}" WHERE id = :comp_id')
                res = await session.execute(stmt, {"comp_id": user_company_id})
            else:
                stmt = text(f'SELECT * FROM "{table.name}"')
                res = await session.execute(stmt)

            rows = res.mappings().all()
            if rows:
                backup_data["tables"][table.name] = [
                    {k: serialize_val(v) for k, v in row.items()}
                    for row in rows
                ]
            else:
                backup_data["tables"][table.name] = []
        except Exception as e:
            await session.rollback()
            logger.warning(f"No se pudo respaldar tabla {table.name}: {e}")

    return backup_data


async def create_cloud_backup(
    session: AsyncSession,
    company_id: Optional[uuid.UUID] = None,
    source: str = "auto"
) -> Dict[str, Any]:
    """
    Genera un respaldo completo y lo sube directamente a DigitalOcean Spaces en la carpeta backups/.
    Aplica rotación automática para mantener un máximo de 30 copias.
    """
    if not is_spaces_configured():
        raise RuntimeError("DigitalOcean Spaces no está configurado en las variables de entorno.")

    now_str = datetime.now(timezone.utc).strftime("%Y-%m-%d_%H%M%S")
    filename = f"respaldo_{source}_{now_str}.json"

    logger.info(f"📦 Generando copia de seguridad {filename}...")
    dump = await dump_database_to_dict(session, company_id=company_id)
    content_str = json.dumps(dump, ensure_ascii=False, indent=2)
    file_bytes = content_str.encode("utf-8")

    upload_res = upload_to_spaces(
        file_bytes=file_bytes,
        filename=filename,
        content_type="application/json; charset=utf-8",
        folder="backups",
        is_private=False
    )

    if not upload_res:
        raise RuntimeError("No se pudo subir la copia de seguridad a DigitalOcean Spaces.")

    # Rotación: mantener máximo 30 copias automáticas
    try:
        await prune_old_backups(max_backups=30)
    except Exception as e:
        logger.warning(f"Error durante rotación de copias antiguas: {e}")

    logger.info(f"✅ Copia de seguridad en la nube completada: {filename} ({len(file_bytes)} bytes)")
    return {
        "status": "success",
        "filename": filename,
        "key": upload_res.get("key"),
        "url": upload_res.get("url"),
        "size_bytes": len(file_bytes),
        "pretty_size": f"{len(file_bytes) / 1024:.1f} KB" if len(file_bytes) < 1024 * 1024 else f"{len(file_bytes) / (1024 * 1024):.2f} MB",
        "created_at": datetime.now(timezone.utc).strftime("%d/%m/%Y %H:%M:%S UTC")
    }


async def prune_old_backups(max_backups: int = 30):
    """Elimina las copias de seguridad más antiguas si superan max_backups."""
    files = list_spaces_files(prefix="backups/")
    if len(files) > max_backups:
        to_delete = files[max_backups:]
        for f in to_delete:
            delete_from_spaces(f["key"])
            logger.info(f"🧹 Rotación: copia de seguridad eliminada: {f['filename']}")


# ===== SCHEDULER DE TAREAS EN SEGUNDO PLANO =====
_scheduler_task: Optional[asyncio.Task] = None


async def _daily_backup_loop():
    """Bucle asíncrono que ejecuta la copia de seguridad diaria automática."""
    from app.core.database import AsyncSessionLocal

    logger.info("⏰ Servicio de copias de seguridad automáticas iniciado (programación diaria).")
    
    # Esperar 60 segundos después de arrancar para no sobrecargar el inicio
    await asyncio.sleep(60)

    last_backup_date: Optional[date] = None

    while True:
        try:
            now_utc = datetime.now(timezone.utc)
            # Ejecutar entre las 08:00 UTC (02:00 AM hora de Guatemala / Centroamérica)
            # O si hoy no se ha ejecutado ninguna copia y Spaces está configurado
            if is_spaces_configured() and now_utc.hour == 8 and last_backup_date != now_utc.date():
                logger.info("⏳ Iniciando respaldo automático programado de las 02:00 AM...")
                async with AsyncSessionLocal() as session:
                    await create_cloud_backup(session, source="auto_diario")
                    last_backup_date = now_utc.date()
                    logger.info("✅ Respaldo automático diario finalizado exitosamente.")
        except asyncio.CancelledError:
            logger.info("🛑 Deteniendo planificador de copias de seguridad...")
            break
        except Exception as e:
            logger.error(f"❌ Error en el proceso de respaldo automático: {e}")

        # Revisar cada 30 minutos
        await asyncio.sleep(1800)


def start_backup_scheduler():
    """Inicia el servicio de respaldo automático en segundo plano."""
    global _scheduler_task
    if _scheduler_task is None or _scheduler_task.done():
        _scheduler_task = asyncio.create_task(_daily_backup_loop())
        logger.info("🚀 Scheduler de respaldos automáticos registrado en background.")


def stop_backup_scheduler():
    """Detiene el planificador si está activo."""
    global _scheduler_task
    if _scheduler_task and not _scheduler_task.done():
        _scheduler_task.cancel()
        _scheduler_task = None
