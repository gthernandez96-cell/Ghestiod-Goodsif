"""API v1 — Recursos Humanos (HR)."""
from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy import select, and_
from pydantic import BaseModel, ConfigDict
from typing import Optional, List
from datetime import datetime, timezone
import uuid

from app.core.database import get_db
from app.core.security import get_current_user
from app.models.hr import Employee, Attendance

router = APIRouter(prefix="/hr")


# ===== SCHEMAS =====
class EmployeeIn(BaseModel):
    name: str
    role: str
    shift: Optional[str] = None
    phone: Optional[str] = None


class EmployeeOut(BaseModel):
    id: uuid.UUID
    name: str
    role: str
    shift: Optional[str]
    phone: Optional[str]
    present: bool

    model_config = ConfigDict(from_attributes=True)


# ===== ENDPOINTS =====
@router.get("/employees", response_model=List[EmployeeOut])
async def list_employees(
    db: AsyncSession = Depends(get_db),
    current_user=Depends(get_current_user),
):
    """Listar empleados de la empresa."""
    stmt = select(Employee).where(
        Employee.company_id == current_user.company_id,
        Employee.is_deleted == False
    ).order_by(Employee.name.asc())
    res = await db.execute(stmt)
    return res.scalars().all()


@router.post("/employees", response_model=EmployeeOut)
async def create_employee(
    data: EmployeeIn,
    db: AsyncSession = Depends(get_db),
    current_user=Depends(get_current_user),
):
    """Registrar un nuevo empleado."""
    emp = Employee(
        company_id=current_user.company_id,
        name=data.name,
        role=data.role,
        shift=data.shift,
        phone=data.phone,
        present=False
    )
    db.add(emp)
    await db.commit()
    await db.refresh(emp)
    return emp


@router.post("/employees/{employee_id}/toggle-attendance")
async def toggle_attendance(
    employee_id: str,
    db: AsyncSession = Depends(get_db),
    current_user=Depends(get_current_user),
):
    """Alternar estado de asistencia del empleado (Check-in / Check-out)."""
    try:
        emp_uuid = uuid.UUID(employee_id)
    except ValueError:
        raise HTTPException(status_code=400, detail="ID de empleado inválido")

    stmt = select(Employee).where(
        Employee.id == emp_uuid,
        Employee.company_id == current_user.company_id,
        Employee.is_deleted == False
    )
    res = await db.execute(stmt)
    emp = res.scalar_one_or_none()
    if not emp:
        raise HTTPException(status_code=404, detail="Empleado no encontrado")

    now = datetime.now(timezone.utc)

    if not emp.present:
        # Check-in: Crear registro de asistencia
        attendance = Attendance(
            employee_id=emp.id,
            check_in=now,
            date=now
        )
        emp.present = True
        db.add(attendance)
        action = "Check-in"
    else:
        # Check-out: Buscar el registro abierto de asistencia más reciente
        stmt_att = select(Attendance).where(
            Attendance.employee_id == emp.id,
            Attendance.check_out.is_(None)
        ).order_by(Attendance.check_in.desc()).limit(1)
        res_att = await db.execute(stmt_att)
        att = res_att.scalar_one_or_none()

        if att:
            att.check_out = now
        emp.present = False
        action = "Check-out"

    await db.commit()
    await db.refresh(emp)
    return {"status": "ok", "present": emp.present, "action": action, "time": now.isoformat()}
