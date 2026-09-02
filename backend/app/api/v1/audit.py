"""API v1 — Auditoría y Seguridad."""
from fastapi import APIRouter, Depends
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy import select
from pydantic import BaseModel, ConfigDict
from typing import List, Optional
import uuid

from app.core.database import get_db
from app.core.security import get_current_user
from app.models.audit import AuditLog

router = APIRouter(prefix="/audit")


# ===== SCHEMAS =====
class AuditLogOut(BaseModel):
    id: str
    timestamp: str
    user: str
    module: str
    action: str
    ip: str
    state: str

    model_config = ConfigDict(from_attributes=True)


# ===== ENDPOINTS =====
@router.get("/logs", response_model=List[AuditLogOut])
async def list_audit_logs(
    db: AsyncSession = Depends(get_db),
    current_user=Depends(get_current_user),
):
    """Listar registros de la bitácora de auditoría."""
    company_uuid = uuid.UUID(str(current_user.company_id))

    # Cargar logs ordenados del más reciente al más antiguo
    stmt = (
        select(AuditLog)
        .where(
            AuditLog.company_id == company_uuid,
            AuditLog.is_deleted == False
        )
        .order_by(AuditLog.timestamp.desc())
        .limit(200)
    )
    res = await db.execute(stmt)
    logs = res.scalars().all()

    # Formatear respuesta
    out = []
    for log in logs:
        # Resolver nombre de usuario si existe relación (o usar fallback del usuario actual)
        user_name = current_user.full_name if log.user_id == current_user.id else "Usuario POS"

        out.append(
            AuditLogOut(
                id=str(log.id),
                timestamp=log.timestamp.strftime("%d/%m/%Y %H:%M:%S") if log.timestamp else "",
                user=user_name,
                module=log.module,
                action=log.action,
                ip=log.ip_address or "127.0.0.1",
                state=log.state
            )
        )
    return out
