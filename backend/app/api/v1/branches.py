"""API v1 — Empresas, Sucursales y Bodegas."""
from fastapi import APIRouter, Depends, HTTPException, Query, Response
from fastapi.responses import FileResponse
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy import select, func, or_, text
from sqlalchemy.orm import selectinload
from pydantic import BaseModel
from typing import Optional, List, Dict, Any
import uuid
import os
import json
from decimal import Decimal
from datetime import datetime, date, timezone

from app.core.database import get_db
from app.core.security import get_current_user, get_password_hash
from app.models.company import Company, Branch, Warehouse, TaxRegime
from app.models.inventory import StockQuant
from app.models.user import User

import logging
logger = logging.getLogger(__name__)

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


class BranchUpdateIn(BaseModel):
    name: Optional[str] = None
    code: Optional[str] = None
    address: Optional[str] = None
    city: Optional[str] = None
    phone: Optional[str] = None
    is_active: Optional[bool] = None
    is_main: Optional[bool] = None


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
        "pos_enable_wholesale": st.get("pos_enable_wholesale", True),
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
        "ticket_footer", "allow_negative_stock", "require_supervisor_pin", "pos_enable_wholesale",
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
    db: AsyncSession = Depends(get_db),
    current_user=Depends(get_current_user),
):
    """Descargar copia de seguridad íntegra de la base de datos (JSON estructurado o archivo SQLite)."""
    now_str = datetime.now().strftime("%Y-%m-%d_%H%M%S")
    db_path = os.path.abspath("pos.db")
    from app.core.config import settings

    # Si es SQLite local y el archivo pos.db existe físicamente, se entrega directamente
    if "postgresql" not in settings.DATABASE_URL and os.path.exists(db_path):
        return FileResponse(
            path=db_path,
            filename=f"backup_pos_empresarial_{now_str}.db",
            media_type="application/x-sqlite3"
        )

    # En PostgreSQL (producción / Dev DB / Managed DB), exportamos un volcado estructurado JSON completo
    from app.models.base import BaseModel

    def serialize_val(val):
        if isinstance(val, (datetime, date)):
            return val.isoformat()
        if isinstance(val, Decimal):
            return float(val)
        if isinstance(val, uuid.UUID):
            return str(val)
        if isinstance(val, bytes):
            return val.hex()
        return val

    user_company_id = getattr(current_user, "company_id", None)
    backup_data = {
        "metadata": {
            "version": "1.0",
            "system": "Ghestiod POS Empresarial",
            "created_at": datetime.now(timezone.utc).isoformat(),
            "company_id": str(user_company_id) if user_company_id else None,
            "user_email": getattr(current_user, "email", "admin")
        },
        "tables": {}
    }

    for table in BaseModel.metadata.sorted_tables:
        try:
            has_company_col = "company_id" in table.c
            if has_company_col and user_company_id:
                stmt = text(f'SELECT * FROM "{table.name}" WHERE company_id = :comp_id')
                res = await db.execute(stmt, {"comp_id": user_company_id})
            elif table.name == "companies" and user_company_id:
                stmt = text(f'SELECT * FROM "{table.name}" WHERE id = :comp_id')
                res = await db.execute(stmt, {"comp_id": user_company_id})
            else:
                stmt = text(f'SELECT * FROM "{table.name}"')
                res = await db.execute(stmt)

            rows = res.mappings().all()
            if rows:
                backup_data["tables"][table.name] = [
                    {k: serialize_val(v) for k, v in row.items()}
                    for row in rows
                ]
            else:
                backup_data["tables"][table.name] = []
        except Exception as e:
            await db.rollback()
            logger.warning(f"No se pudo respaldar tabla {table.name}: {e}")

    content_str = json.dumps(backup_data, ensure_ascii=False, indent=2)
    filename = f"respaldo_empresarial_{now_str}.json"

    return Response(
        content=content_str,
        media_type="application/json; charset=utf-8",
        headers={
            "Content-Disposition": f'attachment; filename="{filename}"'
        }
    )


@router.post("/companies/backup/cloud-now")
async def trigger_cloud_backup_now(
    db: AsyncSession = Depends(get_db),
    current_user=Depends(get_current_user),
):
    """Ejecutar un respaldo inmediato en la nube y subirlo a DigitalOcean Spaces."""
    from app.services.backup_service import create_cloud_backup
    from app.services.spaces_storage import is_spaces_configured

    if not is_spaces_configured():
        raise HTTPException(
            status_code=400,
            detail="DigitalOcean Spaces no está configurado aún. Configura las credenciales en DigitalOcean para activar los respaldos en la nube."
        )

    try:
        company_id = getattr(current_user, "company_id", None)
        result = await create_cloud_backup(db, company_id=company_id, source="manual")
        return result
    except Exception as e:
        logger.error(f"Error generando respaldo en la nube: {e}")
        raise HTTPException(status_code=500, detail=f"Error al generar respaldo en la nube: {str(e)}")


@router.get("/companies/backup/cloud-list")
async def list_cloud_backups_endpoint(
    current_user=Depends(get_current_user),
):
    """Listar los respaldos almacenados en la nube (DigitalOcean Spaces)."""
    from app.services.spaces_storage import list_spaces_files, is_spaces_configured

    if not is_spaces_configured():
        return {
            "configured": False,
            "backups": []
        }

    files = list_spaces_files(prefix="backups/")
    return {
        "configured": True,
        "backups": files
    }


@router.get("/companies/database-storage")
async def get_database_storage_stats(
    db: AsyncSession = Depends(get_db),
    current_user=Depends(get_current_user),
):
    """Consultar estadísticas de almacenamiento, peso en GB/MB y tablas principales de la base de datos."""
    from app.core.config import settings

    is_pg = "postgresql" in settings.DATABASE_URL
    quota_gb = 1.0  # Plan Dev Database de DigitalOcean (1.0 GB Disk, 512 MB RAM)

    TABLE_LABELS = {
        "partners": "Clientes y Proveedores",
        "products": "Catálogo de Productos",
        "sale_orders": "Ventas y Facturación",
        "sale_order_items": "Líneas de Detalle de Ventas",
        "stock_quants": "Inventario en Bodegas",
        "stock_moves": "Kárdex y Movimientos",
        "purchase_orders": "Compras a Proveedores",
        "purchase_order_items": "Detalle de Compras",
        "audit_logs": "Bitácora de Auditoría",
        "account_moves": "Asientos Contables",
        "users": "Usuarios y Colaboradores",
        "cashier_sessions": "Cortes y Sesiones de Caja",
        "payments": "Abonos y Cobranzas",
    }

    size_bytes = 0
    tables_info = []

    if is_pg:
        try:
            # Tamaño global de la base de datos en PostgreSQL
            res = await db.execute(text("SELECT pg_database_size(current_database())"))
            size_bytes = int(res.scalar() or 0)

            # Desglose de tablas principales
            t_res = await db.execute(text("""
                SELECT 
                    s.relname AS table_name,
                    pg_total_relation_size(s.relid) AS total_bytes,
                    COALESCE(st.n_live_tup, 0) AS estimated_rows
                FROM pg_catalog.pg_statio_user_tables s
                LEFT JOIN pg_stat_user_tables st ON s.relid = st.relid
                ORDER BY pg_total_relation_size(s.relid) DESC
                LIMIT 10
            """))
            for r in t_res.fetchall():
                t_bytes = int(r[1] or 0)
                t_name = str(r[0])
                tables_info.append({
                    "table": t_name,
                    "label": TABLE_LABELS.get(t_name, t_name.replace("_", " ").title()),
                    "bytes": t_bytes,
                    "pretty_size": f"{t_bytes / (1024 * 1024):.2f} MB" if t_bytes >= 1024 * 1024 else f"{t_bytes / 1024:.1f} KB",
                    "rows": int(r[2] or 0)
                })
        except Exception as e:
            logger.warning(f"Error consultando tamaño en PostgreSQL: {e}")
    else:
        # Modo SQLite Local
        db_file = os.path.abspath("pos.db")
        if os.path.exists(db_file):
            size_bytes = os.path.getsize(db_file)

        for t_name, t_label in list(TABLE_LABELS.items())[:8]:
            try:
                cnt_res = await db.execute(text(f"SELECT COUNT(*) FROM {t_name}"))
                cnt = int(cnt_res.scalar() or 0)
                tables_info.append({
                    "table": t_name,
                    "label": t_label,
                    "bytes": 0,
                    "pretty_size": f"{cnt} registros",
                    "rows": cnt
                })
            except Exception:
                pass

    size_mb = round(size_bytes / (1024 * 1024), 2)
    size_gb = round(size_bytes / (1024 * 1024 * 1024), 4)
    used_percentage = round((size_gb / quota_gb) * 100, 2)
    free_gb = round(max(0.0, quota_gb - size_gb), 2)

    from app.services.spaces_storage import is_spaces_configured
    spaces_active = is_spaces_configured()
    bucket_name = os.environ.get("SPACES_BUCKET_NAME", "")
    region_name = os.environ.get("SPACES_REGION", "atl1")

    return {
        "status": "success",
        "database_type": "PostgreSQL (DigitalOcean Managed Database)" if is_pg else "SQLite 3 (Desarrollo Local)",
        "is_production_pg": is_pg,
        "size_bytes": size_bytes,
        "size_mb": size_mb,
        "size_gb": size_gb,
        "pretty_size": f"{size_gb:.3f} GB" if size_gb >= 1.0 else f"{size_mb:.2f} MB",
        "quota_gb": quota_gb,
        "free_gb": free_gb,
        "used_percentage": used_percentage,
        "tables": tables_info,
        "spaces_storage": {
            "active": spaces_active,
            "bucket": bucket_name,
            "region": region_name,
            "quota_label": "250 GB" if spaces_active else "No configurado",
            "status_label": "Conectado a DigitalOcean Spaces" if spaces_active else "Almacenamiento Local (Sin Spaces)"
        },
        "last_checked": datetime.now(timezone.utc).strftime("%d/%m/%Y %H:%M:%S UTC")
    }


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
    """Listar sucursales de la empresa activa con conteo de bodegas."""
    stmt = (
        select(Branch)
        .where(Branch.company_id == current_user.company_id, Branch.is_deleted == False)
        .order_by(Branch.is_main.desc(), Branch.name)
    )
    result = await db.execute(stmt)
    branches = result.scalars().all()

    # Contar bodegas por sucursal
    wh_counts = {}
    if branches:
        b_ids = [b.id for b in branches]
        cnt_stmt = select(Warehouse.branch_id, func.count(Warehouse.id)).where(
            Warehouse.branch_id.in_(b_ids),
            Warehouse.is_deleted == False
        ).group_by(Warehouse.branch_id)
        cnt_res = await db.execute(cnt_stmt)
        for b_id, cnt in cnt_res.all():
            wh_counts[b_id] = cnt

    return [
        {
            "id": str(b.id),
            "name": b.name,
            "code": b.code,
            "city": b.city or "Guatemala",
            "is_main": b.is_main,
            "is_active": b.is_active,
            "address": b.address or "",
            "phone": b.phone or "",
            "warehouses_count": wh_counts.get(b.id, 0),
        }
        for b in branches
    ]





@router.post("/branches/")
async def create_branch(
    data: BranchCreateIn,
    db: AsyncSession = Depends(get_db),
    current_user=Depends(get_current_user),
):
    """Crear una nueva sucursal y su bodega principal por defecto."""
    branch = Branch(
        company_id=current_user.company_id,
        name=data.name.strip(),
        code=data.code.strip(),
        address=data.address.strip() if data.address else None,
        city=data.city.strip() if data.city else "Guatemala",
        phone=data.phone.strip() if data.phone else None,
        is_main=data.is_main,
    )
    db.add(branch)
    await db.flush()

    # Bodega por defecto de la sucursal
    wh = Warehouse(
        branch_id=branch.id,
        name=f"Bodega {branch.name}",
        code=f"BOD-{branch.code}",
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


# ===== 4. GESTIÓN INDIVIDUAL DE SUCURSALES (GET, PUT, DELETE) =====
@router.get("/branches/{branch_id}")
async def get_branch(
    branch_id: str,
    db: AsyncSession = Depends(get_db),
    current_user=Depends(get_current_user),
):
    """Obtener detalle de una sucursal específica."""
    try:
        b_uuid = uuid.UUID(branch_id)
    except ValueError:
        raise HTTPException(status_code=400, detail="ID de sucursal inválido")

    stmt = select(Branch).where(
        Branch.id == b_uuid,
        Branch.company_id == current_user.company_id,
        Branch.is_deleted == False
    )
    res = await db.execute(stmt)
    b = res.scalar_one_or_none()
    if not b:
        raise HTTPException(status_code=404, detail="Sucursal no encontrada")

    return {
        "id": str(b.id),
        "name": b.name,
        "code": b.code,
        "city": b.city or "Guatemala",
        "address": b.address or "",
        "phone": b.phone or "",
        "is_main": b.is_main,
        "is_active": b.is_active,
    }


@router.put("/branches/{branch_id}")
async def update_branch(
    branch_id: str,
    data: BranchUpdateIn,
    db: AsyncSession = Depends(get_db),
    current_user=Depends(get_current_user),
):
    """Actualizar datos de una sucursal existente."""
    try:
        b_uuid = uuid.UUID(branch_id)
    except ValueError:
        raise HTTPException(status_code=400, detail="ID de sucursal inválido")

    stmt = select(Branch).where(
        Branch.id == b_uuid,
        Branch.company_id == current_user.company_id,
        Branch.is_deleted == False
    )
    res = await db.execute(stmt)
    b = res.scalar_one_or_none()
    if not b:
        raise HTTPException(status_code=404, detail="Sucursal no encontrada")

    if data.name is not None: b.name = data.name.strip()
    if data.code is not None: b.code = data.code.strip()
    if data.address is not None: b.address = data.address.strip()
    if data.city is not None: b.city = data.city.strip()
    if data.phone is not None: b.phone = data.phone.strip()
    if data.is_active is not None: b.is_active = data.is_active
    if data.is_main is not None: b.is_main = data.is_main

    await db.commit()
    return {"id": str(b.id), "name": b.name, "code": b.code, "status": "updated"}


@router.delete("/branches/{branch_id}")
async def delete_branch(
    branch_id: str,
    db: AsyncSession = Depends(get_db),
    current_user=Depends(get_current_user),
):
    """Eliminar o desactivar una sucursal (valida si es principal o tiene inventario)."""
    try:
        b_uuid = uuid.UUID(branch_id)
    except ValueError:
        raise HTTPException(status_code=400, detail="ID de sucursal inválido")

    stmt = select(Branch).where(
        Branch.id == b_uuid,
        Branch.company_id == current_user.company_id,
        Branch.is_deleted == False
    )
    res = await db.execute(stmt)
    b = res.scalar_one_or_none()
    if not b:
        raise HTTPException(status_code=404, detail="Sucursal no encontrada")

    if b.is_main:
        raise HTTPException(status_code=400, detail="No puedes eliminar la Sucursal Principal del sistema.")

    # Validar si tiene bodegas con existencias físicas
    wh_stmt = select(Warehouse).where(Warehouse.branch_id == b_uuid, Warehouse.is_deleted == False)
    res_wh = await db.execute(wh_stmt)
    warehouses = res_wh.scalars().all()
    wh_ids = [w.id for w in warehouses]
    if wh_ids:
        st_stmt = select(func.sum(StockQuant.qty_on_hand)).where(StockQuant.warehouse_id.in_(wh_ids))
        res_st = await db.execute(st_stmt)
        total_st = res_st.scalar() or 0
        if total_st > 0:
            raise HTTPException(
                status_code=400,
                detail=f"No se puede eliminar la sucursal porque sus bodegas asociadas tienen {float(total_st)} unidades en stock. Debes trasladar o vaciar el inventario antes de eliminarla."
            )

    # Desactivar/eliminar sucursal y sus bodegas
    b.is_deleted = True
    b.is_active = False
    for wh in warehouses:
        wh.is_deleted = True
        wh.is_active = False

    await db.commit()
    return {"status": "deleted", "id": str(b_uuid)}
