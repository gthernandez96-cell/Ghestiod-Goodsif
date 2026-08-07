"""API v1 — Sucursales y Empresa."""
from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy import select
from pydantic import BaseModel
from typing import Optional
from app.core.database import get_db
from app.core.security import get_current_user
from app.models.company import Company, Branch, Warehouse

router = APIRouter(prefix="/branches")


@router.get("/")
async def list_branches(db: AsyncSession = Depends(get_db), current_user=Depends(get_current_user)):
    result = await db.execute(select(Branch).where(Branch.is_deleted == False, Branch.is_active == True))
    branches = result.scalars().all()
    return [{"id": str(b.id), "name": b.name, "code": b.code, "city": b.city, "is_main": b.is_main} for b in branches]


@router.get("/{branch_id}")
async def get_branch(branch_id: str, db: AsyncSession = Depends(get_db), current_user=Depends(get_current_user)):
    result = await db.execute(select(Branch).where(Branch.id == branch_id))
    branch = result.scalar_one_or_none()
    if not branch:
        raise HTTPException(status_code=404, detail="Sucursal no encontrada")
    return {"id": str(branch.id), "name": branch.name, "code": branch.code, "address": branch.address, "city": branch.city, "is_active": branch.is_active}
