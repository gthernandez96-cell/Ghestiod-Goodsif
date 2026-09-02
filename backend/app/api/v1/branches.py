"""API v1 — Empresas, Sucursales y Bodegas."""
from fastapi import APIRouter, Depends, HTTPException, Query
from fastapi.responses import FileResponse
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy import select, func, or_
from sqlalchemy.orm import selectinload
from pydantic import BaseModel
from typing import Optional, List, Dict, Any
import uuid
import os
from datetime import datetime, timezone

from app.core.database import get_db
from app.core.security import get_current_user, get_password_hash
from app.models.company import Company, Branch, Warehouse, TaxRegime
from app.models.inventory import StockQuant
from app.models.user import User

router = APIRouter()


# ===== SCHEMAS =====
class CompanyCreateIn(BaseModel):
    name: str
    trade_name: Optional[str] = None
    nit: str
    tax_regime: str = "general"
    address: Optional[str] = None
    city: Optional[str] = "Guatemala"
    phone: Optional[str] = None
    email: Optional[str] = None
    currency: str = "GTQ"
    logo_url: Optional[str] = None


class CompanyUpdateIn(BaseModel):
    name: Optional[str] = None
    trade_name: Optional[str] = None
    nit: Optional[str] = None
    tax_regime: Optional[str] = None
    address: Optional[str] = None
    city: Optional[str] = None
    phone: Optional[str] = None
    email: Optional[str] = None
    logo_url: Optional[str] = None
    settings: Optional[Dict[str, Any]] = None


class UserCreateIn(BaseModel):
    full_name: str
    email: str
    username: str
    password: str
    is_superuser: bool = False
    current_branch_id: Optional[str] = None


class BranchCreateIn(BaseModel):
    name: str
    code: str
    address: Optional[str] = None
    city: Optional[str] = "Guatemala"
    phone: Optional[str] = None
    is_main: bool = False


class WarehouseCreateIn(BaseModel):
    name: str
    code: str
    branch_id: Optional[str] = None
    address: Optional[str] = None
    is_default: bool = False


class WarehouseUpdateIn(BaseModel):
    name: Optional[str] = None
    code: Optional[str] = None
    address: Optional[str] = None
    is_active: Optional[bool] = None
    is_default: Optional[bool] = None


# ===== 1. COMPANIES (MULTI-EMPRESA & CONFIGURACIONES) =====
@router.get("/companies/current")
async def get_current_company_settings(
    db: AsyncSession = Depends(get_db),
    current_user=Depends(get_current_user),
):
    """Obtener los datos fiscales y ajustes completos de la empresa activa."""
    c_uuid = uuid.UUID(str(current_user.company_id))
    stmt = select(Company).where(Company.id == c_uuid)
    res = await db.execute(stmt)
    c = res.scalar_one_or_none()
    if not c:
        raise HTTPException(status_code=404, detail="Empresa no encontrada")

    st = c.settings or {}
    return {
        "id": str(c.id),
        "name": c.name,
        "trade_name": c.trade_name or c.name,
        "nit": c.nit,
        "tax_regime": c.tax_regime.value if hasattr(c.tax_regime, 'value') else str(c.tax_regime),
        "address": c.address or "",
        "city": c.city or "Guatemala",
        "phone": c.phone or "",
        "email": c.email or "",
        "currency": c.currency or "GTQ",
        "timezone": c.timezone or "America/Guatemala",
        "logo_url": c.logo_url or "",
        "fel_provider": st.get("fel_provider", "INFILE"),
        "fel_user": st.get("fel_user", "USUARIO_FEL_DEMO"),
        "fel_key": st.get("fel_key", ""),
        "fel_environment": st.get("fel_environment", "sandbox"),
        "sat_establishment_code": st.get("sat_establishment_code", "1"),
        "pos_ticket_format": st.get("pos_ticket_format", "80mm"),
        "ticket_header": st.get("ticket_header", ""),
        "ticket_footer": st.get("ticket_footer", "¡Gracias por su preferencia!"),
        "allow_negative_stock": st.get("allow_negative_stock", False),
        "require_supervisor_pin": st.get("require_supervisor_pin", True),
        # Políticas de Inventario
        "inventory_valuation_method": st.get("inventory_valuation_method", "average"),
        "low_stock_threshold": st.get("low_stock_threshold", 5),
        "require_lots_expiration": st.get("require_lots_expiration", False),
        # Métodos de Pago
        "payment_cash": st.get("payment_cash", True),
        "payment_card": st.get("payment_card", True),
        "payment_transfer": st.get("payment_transfer", True),
        "payment_check": st.get("payment_check", True),
        "payment_credit": st.get("payment_credit", True),
        # Moneda Secundaria
        "enable_usd_currency": st.get("enable_usd_currency", False),
        "usd_exchange_rate": st.get("usd_exchange_rate", 7.75),
        # Notificaciones & Automatizaciones
        "auto_send_whatsapp_ticket": st.get("auto_send_whatsapp_ticket", True),
        "auto_send_email_ticket": st.get("auto_send_email_ticket", False),
        "email_alerts_enabled": st.get("email_alerts_enabled", False),
        # Programa de Lealtad & Puntos
        "enable_loyalty_program": st.get("enable_loyalty_program", False),
        "loyalty_points_per_quetzal": st.get("loyalty_points_per_quetzal", 1.0),
        "loyalty_redeem_rate": st.get("loyalty_redeem_rate", 10.0),
    }


@router.put("/companies/current")
async def update_current_company_settings(
    data: Dict[str, Any],
    db: AsyncSession = Depends(get_db),
    current_user=Depends(get_current_user),
):
    """Actualizar datos fiscales, logotipo y parámetros generales de la empresa."""
    c_uuid = uuid.UUID(str(current_user.company_id))
    stmt = select(Company).where(Company.id == c_uuid)
    res = await db.execute(stmt)
    c = res.scalar_one_or_none()
    if not c:
        raise HTTPException(status_code=404, detail="Empresa no encontrada")

    if "name" in data and data["name"]: c.name = data["name"]
    if "trade_name" in data and data["trade_name"]: c.trade_name = data["trade_name"]
    if "nit" in data and data["nit"]: c.nit = data["nit"]
    if "tax_regime" in data:
        try:
            c.tax_regime = TaxRegime(data["tax_regime"])
        except Exception:
            pass
    if "address" in data: c.address = data["address"]
    if "city" in data: c.city = data["city"]
    if "phone" in data: c.phone = data["phone"]
    if "email" in data: c.email = data["email"]
    if "logo_url" in data: c.logo_url = data["logo_url"]

    # Guardar campos extra en settings
    st = dict(c.settings or {})
    extra_fields = [
        "fel_provider", "fel_user", "fel_key", "fel_environment",
        "sat_establishment_code", "pos_ticket_format", "ticket_header",
        "ticket_footer", "allow_negative_stock", "require_supervisor_pin",
        "inventory_valuation_method", "low_stock_threshold", "require_lots_expiration",
        "payment_cash", "payment_card", "payment_transfer", "payment_check", "payment_credit",
        "enable_usd_currency", "usd_exchange_rate", "auto_send_whatsapp_ticket",
        "auto_send_email_ticket", "email_alerts_enabled",
        "enable_loyalty_program", "loyalty_points_per_quetzal", "loyalty_redeem_rate"
    ]
    for k in extra_fields:
        if k in data:
            st[k] = data[k]
    c.settings = st

    await db.commit()
    return {"status": "saved", "message": "Configuración guardada exitosamente"}


@router.get("/companies/backup/download")
async def download_database_backup(
    current_user=Depends(get_current_user),
):
    """Descargar copia de seguridad íntegra de la base de datos (pos.db)."""
    db_path = os.path.abspath("pos.db")
    if not os.path.exists(db_path):
        raise HTTPException(status_code=404, detail="Archivo de base de datos no encontrado")

    now_str = datetime.now().strftime("%Y-%m-%d_%H%M%S")
    filename = f"backup_pos_empresarial_{now_str}.db"

    return FileResponse(
        path=db_path,
        filename=filename,
        media_type="application/x-sqlite3"
    )


@router.get("/companies/users")
async def list_company_users(
    db: AsyncSession = Depends(get_db),
    current_user=Depends(get_current_user),
):
    """Listar usuarios del sistema para la vista de configuraciones."""
    c_uuid = uuid.UUID(str(current_user.company_id))
    stmt = (
        select(User)
        .where(
            User.company_id == c_uuid,
            User.is_deleted == False
        )
        .order_by(User.full_name)
    )
    res = await db.execute(stmt)
    users = res.scalars().all()

    # Obtener nombres de sucursales
    b_stmt = select(Branch).where(Branch.company_id == c_uuid, Branch.is_deleted == False)
    b_res = await db.execute(b_stmt)
    branch_map = {b.id: b.name for b in b_res.scalars().all()}

    return [
        {
            "id": str(u.id),
            "username": u.username,
            "full_name": u.full_name,
            "email": u.email,
            "is_superuser": u.is_superuser,
            "is_active": u.is_active,
            "role": "Super Administrador" if u.is_superuser else "Usuario / Cajero",
            "branch_id": str(u.current_branch_id) if u.current_branch_id else None,
            "branch_name": branch_map.get(u.current_branch_id, "Sucursal Central"),
            "created_at": u.created_at.strftime("%Y-%m-%d") if u.created_at else ""
        }
        for u in users
    ]


@router.post("/companies/users")
async def create_company_user(
    data: UserCreateIn,
    db: AsyncSession = Depends(get_db),
    current_user=Depends(get_current_user),
):
    """Crear un nuevo usuario colaborador en la empresa activa."""
    c_uuid = uuid.UUID(str(current_user.company_id))

    # Verificar existencia
    chk_stmt = select(User).where(
        User.company_id == c_uuid,
        (User.username == data.username) | (User.email == data.email),
        User.is_deleted == False
    )
    chk_res = await db.execute(chk_stmt)
    if chk_res.scalar_one_or_none():
        raise HTTPException(status_code=400, detail="El nombre de usuario o correo ya está registrado.")

    branch_uuid = None
    if data.current_branch_id:
        try:
            branch_uuid = uuid.UUID(data.current_branch_id)
        except ValueError:
            pass

    user = User(
        company_id=c_uuid,
        full_name=data.full_name,
        email=data.email,
        username=data.username,
        hashed_password=get_password_hash(data.password),
        is_superuser=data.is_superuser,
        is_active=True,
        current_branch_id=branch_uuid
    )
    db.add(user)
    await db.commit()
    await db.refresh(user)

    return {"id": str(user.id), "username": user.username, "full_name": user.full_name, "status": "created"}


@router.post("/companies/users/{user_id}/toggle-status")
async def toggle_user_status(
    user_id: str,
    db: AsyncSession = Depends(get_db),
    current_user=Depends(get_current_user),
):
    """Activar o desactivar el acceso de un usuario."""
    try:
        u_uuid = uuid.UUID(user_id)
    except ValueError:
        raise HTTPException(status_code=400, detail="ID de usuario inválido")

    if u_uuid == current_user.id:
        raise HTTPException(status_code=400, detail="No puedes desactivar tu propio usuario activo.")

    stmt = select(User).where(User.id == u_uuid, User.company_id == current_user.company_id)
    res = await db.execute(stmt)
    user = res.scalar_one_or_none()
    if not user:
        raise HTTPException(status_code=404, detail="Usuario no encontrado")

    user.is_active = not user.is_active
    await db.commit()

    return {"id": str(user.id), "is_active": user.is_active}


@router.get("/companies")
async def list_companies(
    db: AsyncSession = Depends(get_db),
    current_user=Depends(get_current_user),
):
    """Listar todas las empresas registradas en el sistema."""
    stmt = select(Company).where(Company.is_active == True, Company.is_deleted == False).order_by(Company.name)
    res = await db.execute(stmt)
    companies = res.scalars().all()
    return [
        {
            "id": str(c.id),
            "name": c.name,
            "trade_name": c.trade_name or c.name,
            "nit": c.nit,
            "tax_regime": c.tax_regime.value if hasattr(c.tax_regime, 'value') else str(c.tax_regime),
            "address": c.address or "",
            "city": c.city or "Guatemala",
            "phone": c.phone or "",
            "email": c.email or "",
            "currency": c.currency or "GTQ",
            "logo_url": c.logo_url,
            "is_current": c.id == current_user.company_id,
        }
        for c in companies
    ]


@router.post("/companies")
async def create_company(
    data: CompanyCreateIn,
    db: AsyncSession = Depends(get_db),
    current_user=Depends(get_current_user),
):
    """Crear una nueva empresa / entidad jurídica en el sistema."""
    regime = TaxRegime.GENERAL
    try:
        regime = TaxRegime(data.tax_regime)
    except ValueError:
        pass

    company = Company(
        name=data.name,
        trade_name=data.trade_name or data.name,
        nit=data.nit,
        tax_regime=regime,
        address=data.address,
        city=data.city,
        phone=data.phone,
        email=data.email,
        currency=data.currency,
        logo_url=data.logo_url,
    )
    db.add(company)
    await db.flush()

    # Crear automáticamente sucursal principal y bodega por defecto
    branch = Branch(
        company_id=company.id,
        name=f"Sucursal Central — {company.name}",
        code="SUC-001",
        is_main=True,
        address=data.address,
        city=data.city,
    )
    db.add(branch)
    await db.flush()

    wh = Warehouse(
        branch_id=branch.id,
        name="Bodega Principal",
        code="BOD-001",
        is_default=True,
        address=data.address,
    )
    db.add(wh)
    await db.commit()

    return {"id": str(company.id), "name": company.name, "nit": company.nit, "status": "created"}


@router.put("/companies/{company_id}")
async def update_company(
    company_id: str,
    data: CompanyUpdateIn,
    db: AsyncSession = Depends(get_db),
    current_user=Depends(get_current_user),
):
    """Actualizar datos de una empresa."""
    try:
        c_uuid = uuid.UUID(company_id)
    except ValueError:
        raise HTTPException(status_code=400, detail="ID de empresa inválido")

    stmt = select(Company).where(Company.id == c_uuid)
    res = await db.execute(stmt)
    company = res.scalar_one_or_none()
    if not company:
        raise HTTPException(status_code=404, detail="Empresa no encontrada")

    if data.name is not None: company.name = data.name
    if data.trade_name is not None: company.trade_name = data.trade_name
    if data.nit is not None: company.nit = data.nit
    if data.tax_regime is not None:
        try:
            company.tax_regime = TaxRegime(data.tax_regime)
        except ValueError:
            pass
    if data.address is not None: company.address = data.address
    if data.city is not None: company.city = data.city
    if data.phone is not None: company.phone = data.phone
    if data.email is not None: company.email = data.email
    if data.logo_url is not None: company.logo_url = data.logo_url

    await db.commit()
    return {"id": str(company.id), "status": "updated"}


@router.post("/companies/switch/{company_id}")
async def switch_company(
    company_id: str,
    db: AsyncSession = Depends(get_db),
    current_user=Depends(get_current_user),
):
    """Cambiar la empresa activa del usuario actual."""
    try:
        c_uuid = uuid.UUID(company_id)
    except ValueError:
        raise HTTPException(status_code=400, detail="ID de empresa inválido")

    stmt = select(Company).where(Company.id == c_uuid, Company.is_active == True)
    res = await db.execute(stmt)
    company = res.scalar_one_or_none()
    if not company:
        raise HTTPException(status_code=404, detail="Empresa no encontrada o inactiva")

    # Asignar a usuario
    current_user.company_id = company.id

    # Asignar sucursal por defecto de esa empresa
    b_stmt = select(Branch).where(Branch.company_id == company.id, Branch.is_main == True).limit(1)
    b_res = await db.execute(b_stmt)
    main_b = b_res.scalar_one_or_none()
    if main_b:
        current_user.current_branch_id = main_b.id

    await db.commit()
    return {"status": "switched", "company_id": str(company.id), "company_name": company.name}


# ===== 2. SUCURSALES (BRANCHES) =====
@router.get("/branches/")
async def list_branches(
    db: AsyncSession = Depends(get_db),
    current_user=Depends(get_current_user),
):
    """Listar sucursales de la empresa activa."""
    stmt = (
        select(Branch)
        .where(Branch.company_id == current_user.company_id, Branch.is_deleted == False, Branch.is_active == True)
        .order_by(Branch.is_main.desc(), Branch.name)
    )
    result = await db.execute(stmt)
    branches = result.scalars().all()
    return [
        {
            "id": str(b.id),
            "name": b.name,
            "code": b.code,
            "city": b.city or "Guatemala",
            "is_main": b.is_main,
            "address": b.address or ""
        }
        for b in branches
    ]


@router.post("/branches/")
async def create_branch(
    data: BranchCreateIn,
    db: AsyncSession = Depends(get_db),
    current_user=Depends(get_current_user),
):
    """Crear una nueva sucursal."""
    branch = Branch(
        company_id=current_user.company_id,
        name=data.name,
        code=data.code,
        address=data.address,
        city=data.city,
        phone=data.phone,
        is_main=data.is_main,
    )
    db.add(branch)
    await db.flush()

    # Bodega por defecto de la sucursal
    wh = Warehouse(
        branch_id=branch.id,
        name=f"Bodega {data.name}",
        code=f"BOD-{data.code}",
        is_default=True,
    )
    db.add(wh)
    await db.commit()

    return {"id": str(branch.id), "name": branch.name, "code": branch.code, "status": "created"}


# ===== 3. BODEGAS (WAREHOUSES) =====
@router.get("/warehouses")
@router.get("/branches/warehouses")
async def list_warehouses(
    branch_id: Optional[str] = Query(None),
    db: AsyncSession = Depends(get_db),
    current_user=Depends(get_current_user),
):
    """Listar bodegas/almacenes de la empresa activa."""
    stmt = (
        select(Warehouse)
        .join(Branch)
        .options(selectinload(Warehouse.branch))
        .where(Branch.company_id == current_user.company_id, Warehouse.is_deleted == False)
        .order_by(Warehouse.is_default.desc(), Warehouse.name)
    )

    if branch_id:
        try:
            stmt = stmt.where(Warehouse.branch_id == uuid.UUID(branch_id))
        except ValueError:
            pass

    res = await db.execute(stmt)
    warehouses = res.scalars().all()

    # Conteo de existencias y productos por bodega
    wh_ids = [w.id for w in warehouses]
    quant_map = {}
    if wh_ids:
        q_stmt = (
            select(
                StockQuant.warehouse_id,
                func.count(StockQuant.id).label("product_count"),
                func.sum(StockQuant.qty_on_hand).label("total_stock")
            )
            .where(StockQuant.warehouse_id.in_(wh_ids))
            .group_by(StockQuant.warehouse_id)
        )
        q_res = await db.execute(q_stmt)
        for row in q_res.all():
            quant_map[row.warehouse_id] = {
                "product_count": int(row.product_count or 0),
                "total_stock": float(row.total_stock or 0)
            }

    return [
        {
            "id": str(w.id),
            "name": w.name,
            "code": w.code,
            "address": w.address or "",
            "branch_id": str(w.branch_id),
            "branch_name": w.branch.name if w.branch else "Sucursal Principal",
            "is_default": w.is_default,
            "is_active": w.is_active,
            "product_count": quant_map.get(w.id, {}).get("product_count", 0),
            "total_stock": quant_map.get(w.id, {}).get("total_stock", 0.0),
        }
        for w in warehouses
    ]


@router.get("/branches/warehouses/{warehouse_id}")
async def get_warehouse(
    warehouse_id: str,
    db: AsyncSession = Depends(get_db),
    current_user=Depends(get_current_user),
):
    """Obtener detalle y existencias de una bodega específica."""
    try:
        w_uuid = uuid.UUID(warehouse_id)
    except ValueError:
        raise HTTPException(status_code=400, detail="ID de bodega inválido")

    stmt = select(Warehouse).join(Branch).options(selectinload(Warehouse.branch)).where(
        Warehouse.id == w_uuid, Branch.company_id == current_user.company_id
    )
    res = await db.execute(stmt)
    w = res.scalar_one_or_none()
    if not w:
        raise HTTPException(status_code=404, detail="Bodega no encontrada")

    # Obtener stock total y productos
    q_stmt = (
        select(
            func.count(StockQuant.id).label("product_count"),
            func.sum(StockQuant.qty_on_hand).label("total_stock")
        )
        .where(StockQuant.warehouse_id == w_uuid)
    )
    q_res = await db.execute(q_stmt)
    q_data = q_res.one()

    return {
        "id": str(w.id),
        "name": w.name,
        "code": w.code,
        "address": w.address or "",
        "branch_id": str(w.branch_id),
        "branch_name": w.branch.name if w.branch else "Sucursal Principal",
        "is_default": w.is_default,
        "is_active": w.is_active,
        "product_count": int(q_data.product_count or 0),
        "total_stock": float(q_data.total_stock or 0.0),
    }


@router.post("/branches/warehouses")
async def create_warehouse(
    data: WarehouseCreateIn,
    db: AsyncSession = Depends(get_db),
    current_user=Depends(get_current_user),
):
    """Crear una nueva bodega/almacén."""
    b_uuid = None
    if data.branch_id:
        try:
            b_uuid = uuid.UUID(data.branch_id)
        except ValueError:
            pass

    if not b_uuid:
        b_stmt = select(Branch).where(Branch.company_id == current_user.company_id, Branch.is_main == True).limit(1)
        b_res = await db.execute(b_stmt)
        main_b = b_res.scalar_one_or_none()
        b_uuid = main_b.id if main_b else current_user.current_branch_id

    wh = Warehouse(
        branch_id=b_uuid,
        name=data.name,
        code=data.code,
        address=data.address,
        is_default=data.is_default,
    )
    db.add(wh)
    await db.commit()
    await db.refresh(wh)

    return {"id": str(wh.id), "name": wh.name, "code": wh.code, "status": "created"}


@router.put("/branches/warehouses/{warehouse_id}")
async def update_warehouse(
    warehouse_id: str,
    data: WarehouseUpdateIn,
    db: AsyncSession = Depends(get_db),
    current_user=Depends(get_current_user),
):
    """Actualizar datos de una bodega."""
    try:
        w_uuid = uuid.UUID(warehouse_id)
    except ValueError:
        raise HTTPException(status_code=400, detail="ID de bodega inválido")

    stmt = select(Warehouse).join(Branch).where(Warehouse.id == w_uuid, Branch.company_id == current_user.company_id)
    res = await db.execute(stmt)
    wh = res.scalar_one_or_none()
    if not wh:
        raise HTTPException(status_code=404, detail="Bodega no encontrada")

    if data.name is not None: wh.name = data.name
    if data.code is not None: wh.code = data.code
    if data.address is not None: wh.address = data.address
    if data.is_active is not None: wh.is_active = data.is_active
    if data.is_default is not None: wh.is_default = data.is_default

    await db.commit()
    return {"id": str(wh.id), "status": "updated"}
