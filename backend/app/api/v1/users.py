"""API v1 — Gestión Integral de Usuarios y Roles Predeterminados."""
from fastapi import APIRouter, Depends, HTTPException, Query, status
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy import select, func, or_
from sqlalchemy.orm import selectinload
from pydantic import BaseModel, EmailStr
from typing import Optional, List, Dict, Any
import uuid
from datetime import datetime, timezone

from app.core.database import get_db
from app.core.security import get_current_user, get_password_hash
from app.models.company import Company, Branch
from app.models.user import User, Role, Permission, user_roles

router = APIRouter()


# ===== ROLES PREDETERMINADOS DEL SISTEMA =====
PREDEFINED_ROLES = [
    {
        "code": "admin",
        "name": "Super Administrador",
        "badge_color": "#714B67",
        "badge_bg": "#f4eff3",
        "icon": "ph-crown",
        "description": "Acceso total e irrestricto a todos los módulos: configuración fiscal FEL, contabilidad, auditoría, usuarios y cierres maestros.",
        "permissions": [
            {"module": "POS & Ventas", "actions": "Apertura, Cobros, Anulaciones, Descuentos, Reimpresión"},
            {"module": "Inventarios", "actions": "Creación, Costos, Ajustes, Traslados, Valoración"},
            {"module": "Compras & AP", "actions": "Órdenes de Compra, Recepciones, Facturas Proveedor, Pagos"},
            {"module": "Créditos & AR", "actions": "Líneas de Crédito, Facturas por Cobrar, Abonos, Estados de Cuenta"},
            {"module": "Contabilidad & FEL", "actions": "Partidas, Nomenclatura, P&L, Emisión FEL SAT, Certificación"},
            {"module": "Configuración & Usuarios", "actions": "Empresas, Sucursales, Roles, Permisos, Bitácoras, Backups"}
        ]
    },
    {
        "code": "store_manager",
        "name": "Gerente de Sucursal / Supervisor",
        "badge_color": "#0284c7",
        "badge_bg": "#e0f2fe",
        "icon": "ph-shield-check",
        "description": "Supervisión de tienda y personal, autorización de descuentos especiales con PIN, arqueos ciegos y reportes operativos.",
        "permissions": [
            {"module": "POS & Ventas", "actions": "Apertura/Cierre, Autorizaciones PIN, Descuentos, Arqueos"},
            {"module": "Inventarios", "actions": "Recepción en Bodega, Solicitud de Traslados, Auditoría Física"},
            {"module": "Compras & AP", "actions": "Aprobación de Compras Locales, Fichas de Proveedores"},
            {"module": "Créditos & AR", "actions": "Consulta de Saldos de Clientes, Registro de Cobranzas"},
            {"module": "Contabilidad & FEL", "actions": "Consulta de Libro de Ventas Diario"},
            {"module": "Configuración & Usuarios", "actions": "Gestión de Turnos y Marcaje de Empleados"}
        ]
    },
    {
        "code": "cashier",
        "name": "Cajero / Operador POS",
        "badge_color": "#15803d",
        "badge_bg": "#dcfce7",
        "icon": "ph-shopping-cart",
        "description": "Operación de ventas en mostrador, emisión de tickets y facturas FEL. Restricción estricta: Bloqueado para crear o modificar productos y alterar precios de venta sin autorización.",
        "permissions": [
            {"module": "POS & Ventas", "actions": "Cobros en Caja a precios de catálogo (Bloqueado: Modificación manual de precios)"},
            {"module": "Inventarios", "actions": "Consulta de Stock únicamente (Bloqueado: Creación, Edición y Precios)"},
            {"module": "Compras & AP", "actions": "Sin acceso"},
            {"module": "Créditos & AR", "actions": "Recepción de Abonos simples de Clientes"},
            {"module": "Contabilidad & FEL", "actions": "Generación de Ticket Factura FEL de la venta"},
            {"module": "Configuración & Usuarios", "actions": "Sin acceso"}
        ]
    },
    {
        "code": "inventory_manager",
        "name": "Encargado de Inventario / Bodeguero",
        "badge_color": "#c2410c",
        "badge_bg": "#ffedd5",
        "icon": "ph-package",
        "description": "Administración de bodegas, recepción de compras, despacho de traslados, kardex, códigos de barra y conteos físicos.",
        "permissions": [
            {"module": "POS & Ventas", "actions": "Sin acceso directo a cobros"},
            {"module": "Inventarios", "actions": "Catálogo Maestro, Creación SKUs, Traslados, Ajustes, Kardex"},
            {"module": "Compras & AP", "actions": "Recepción Física de Mercadería y Verificación de OC"},
            {"module": "Créditos & AR", "actions": "Sin acceso"},
            {"module": "Contabilidad & FEL", "actions": "Valoración de Stock por Bodega"},
            {"module": "Configuración & Usuarios", "actions": "Gestión de Bodegas y Ubicaciones"}
        ]
    },
    {
        "code": "accountant",
        "name": "Contador / Finanzas",
        "badge_color": "#9333ea",
        "badge_bg": "#f3e8ff",
        "icon": "ph-scales",
        "description": "Gestión financiera, Cuentas por Cobrar (AR), Cuentas por Pagar (AP), emisión y auditoría fiscal FEL SAT, libro diario y P&L.",
        "permissions": [
            {"module": "POS & Ventas", "actions": "Consulta de Ventas Consolidadas y Medios de Pago"},
            {"module": "Inventarios", "actions": "Valoración Financiera de Inventarios y Costo de Venta"},
            {"module": "Compras & AP", "actions": "Registro de Facturas Proveedor, Liquidaciones y Cheques"},
            {"module": "Créditos & AR", "actions": "Control de Cartera, Antigüedad de Saldos, Emisión de Recibos"},
            {"module": "Contabilidad & FEL", "actions": "Libro Diario, Balance de Comprobación, P&L, Conciliaciones SAT"},
            {"module": "Configuración & Usuarios", "actions": "Configuraciones Contables e Impuestos"}
        ]
    }
]


# ===== SCHEMAS =====
class UserCreateRequest(BaseModel):
    full_name: str
    username: str
    email: EmailStr
    password: str
    role_code: str = "cashier"
    current_branch_id: Optional[str] = None
    supervisor_pin: Optional[str] = None
    phone: Optional[str] = None


class UserUpdateRequest(BaseModel):
    full_name: Optional[str] = None
    email: Optional[EmailStr] = None
    role_code: Optional[str] = None
    current_branch_id: Optional[str] = None
    supervisor_pin: Optional[str] = None
    phone: Optional[str] = None
    is_active: Optional[bool] = None


class ResetPasswordRequest(BaseModel):
    new_password: str


# ===== SEEDING HELPER =====
async def ensure_roles_seeded(db: AsyncSession):
    """Asegura que los 5 roles predeterminados existan en la base de datos."""
    for r_data in PREDEFINED_ROLES:
        stmt = select(Role).where(Role.code == r_data["code"])
        res = await db.execute(stmt)
        role = res.scalar_one_or_none()
        if not role:
            role = Role(
                name=r_data["name"],
                code=r_data["code"],
                description=r_data["description"],
                is_system=True
            )
            db.add(role)
    await db.commit()


# ===== ENDPOINTS =====

@router.get("/roles")
async def list_roles(
    db: AsyncSession = Depends(get_db),
    current_user=Depends(get_current_user)
):
    """Listar todos los roles predeterminados del sistema con su descripción y permisos."""
    await ensure_roles_seeded(db)
    return PREDEFINED_ROLES


@router.get("/roles/matrix")
async def get_roles_permissions_matrix(
    db: AsyncSession = Depends(get_db),
    current_user=Depends(get_current_user)
):
    """Obtener la matriz completa de roles y permisos configurados."""
    await ensure_roles_seeded(db)
    return {
        "roles": PREDEFINED_ROLES,
        "modules": [
            "POS & Ventas",
            "Inventarios",
            "Compras & AP",
            "Créditos & AR",
            "Contabilidad & FEL",
            "Configuración & Usuarios"
        ]
    }


@router.get("/users/kpis")
async def get_users_kpis(
    db: AsyncSession = Depends(get_db),
    current_user=Depends(get_current_user)
):
    """Obtener resumen ejecutivo y métricas de usuarios."""
    c_uuid = uuid.UUID(str(current_user.company_id))

    stmt = select(User).options(selectinload(User.roles)).where(
        User.company_id == c_uuid,
        User.is_deleted == False
    )
    res = await db.execute(stmt)
    users = res.scalars().all()

    total_users = len(users)
    active_users = sum(1 for u in users if u.is_active)
    
    cashiers_count = 0
    admins_count = 0
    managers_count = 0
    inventory_count = 0
    accountants_count = 0

    for u in users:
        role_codes = [r.code for r in u.roles]
        if u.is_superuser or "admin" in role_codes:
            admins_count += 1
        elif "store_manager" in role_codes:
            managers_count += 1
        elif "inventory_manager" in role_codes:
            inventory_count += 1
        elif "accountant" in role_codes:
            accountants_count += 1
        else:
            cashiers_count += 1

    return {
        "total_users": total_users,
        "active_users": active_users,
        "cashiers_count": cashiers_count,
        "admins_count": admins_count,
        "managers_count": managers_count,
        "inventory_count": inventory_count,
        "accountants_count": accountants_count
    }


@router.get("/users")
async def list_users(
    search: Optional[str] = Query(None),
    role: Optional[str] = Query(None),
    branch_id: Optional[str] = Query(None),
    db: AsyncSession = Depends(get_db),
    current_user=Depends(get_current_user)
):
    """Listar colaboradores registrados con roles asignados, sucursal y estado."""
    c_uuid = uuid.UUID(str(current_user.company_id))
    await ensure_roles_seeded(db)

    # Cargar sucursales para nombres
    b_stmt = select(Branch).where(Branch.company_id == c_uuid)
    b_res = await db.execute(b_stmt)
    branches = {b.id: b.name for b in b_res.scalars().all()}

    stmt = select(User).options(selectinload(User.roles)).where(
        User.company_id == c_uuid,
        User.is_deleted == False
    ).order_by(User.created_at.desc())

    res = await db.execute(stmt)
    users = res.scalars().all()

    result = []
    for u in users:
        # Extraer rol principal
        role_obj = u.roles[0] if u.roles else None
        role_code = "admin" if u.is_superuser else (role_obj.code if role_obj else "cashier")
        
        # Metadata del rol
        role_meta = next((r for r in PREDEFINED_ROLES if r["code"] == role_code), {
            "code": role_code,
            "name": "Super Administrador" if u.is_superuser else (role_obj.name if role_obj else "Cajero POS"),
            "badge_color": "#714B67",
            "badge_bg": "#f4eff3",
            "icon": "ph-user"
        })

        st = u.settings or {}
        supervisor_pin = st.get("supervisor_pin", "")

        user_dict = {
            "id": str(u.id),
            "full_name": u.full_name,
            "username": u.username,
            "email": u.email,
            "phone": u.phone or "",
            "is_active": u.is_active,
            "is_superuser": u.is_superuser,
            "role_code": role_code,
            "role_name": role_meta["name"],
            "role_badge_color": role_meta["badge_color"],
            "role_badge_bg": role_meta["badge_bg"],
            "role_icon": role_meta["icon"],
            "current_branch_id": str(u.current_branch_id) if u.current_branch_id else None,
            "branch_name": branches.get(u.current_branch_id, "Sucursal Central (Principal)"),
            "supervisor_pin": "••••" if supervisor_pin else "No asignado",
            "has_supervisor_pin": bool(supervisor_pin),
            "created_at": u.created_at.strftime("%d/%m/%Y") if u.created_at else ""
        }

        # Filtrar por búsqueda
        if search:
            q = search.lower()
            if not (q in u.full_name.lower() or q in u.username.lower() or q in u.email.lower()):
                continue

        # Filtrar por rol
        if role and role != "all" and user_dict["role_code"] != role:
            continue

        # Filtrar por sucursal
        if branch_id and branch_id != "all" and user_dict["current_branch_id"] != branch_id:
            continue

        result.append(user_dict)

    return result


@router.post("/users", status_code=status.HTTP_201_CREATED)
async def create_user(
    data: UserCreateRequest,
    db: AsyncSession = Depends(get_db),
    current_user=Depends(get_current_user)
):
    """Crear un nuevo colaborador con su rol predeterminado y sucursal."""
    c_uuid = uuid.UUID(str(current_user.company_id))
    await ensure_roles_seeded(db)

    # Validar duplicados
    chk_stmt = select(User).where(
        User.company_id == c_uuid,
        User.is_deleted == False,
        or_(User.username == data.username.strip(), User.email == data.email.strip())
    )
    chk_res = await db.execute(chk_stmt)
    if chk_res.scalar_one_or_none():
        raise HTTPException(
            status_code=400,
            detail="El nombre de usuario o correo electrónico ya está registrado en la empresa."
        )

    branch_uuid = None
    if data.current_branch_id:
        try:
            branch_uuid = uuid.UUID(data.current_branch_id)
        except ValueError:
            pass

    is_admin = (data.role_code == "admin")

    st = {}
    if data.supervisor_pin and data.supervisor_pin.strip():
        st["supervisor_pin"] = data.supervisor_pin.strip()

    user = User(
        company_id=c_uuid,
        full_name=data.full_name.strip(),
        username=data.username.strip(),
        email=data.email.strip(),
        hashed_password=get_password_hash(data.password),
        phone=data.phone,
        is_active=True,
        is_superuser=is_admin,
        current_branch_id=branch_uuid,
        settings=st
    )

    # Asignar Rol en la base de datos
    role_stmt = select(Role).where(Role.code == data.role_code)
    role_res = await db.execute(role_stmt)
    role = role_res.scalar_one_or_none()
    if role:
        user.roles.append(role)

    db.add(user)
    await db.commit()
    await db.refresh(user)

    return {
        "id": str(user.id),
        "full_name": user.full_name,
        "username": user.username,
        "role": data.role_code,
        "message": "Usuario colaborador creado exitosamente."
    }


@router.put("/users/{user_id}")
async def update_user(
    user_id: str,
    data: UserUpdateRequest,
    db: AsyncSession = Depends(get_db),
    current_user=Depends(get_current_user)
):
    """Actualizar datos, rol o sucursal de un usuario."""
    try:
        u_uuid = uuid.UUID(user_id)
    except ValueError:
        raise HTTPException(status_code=400, detail="ID de usuario inválido.")

    stmt = select(User).options(selectinload(User.roles)).where(
        User.id == u_uuid,
        User.company_id == current_user.company_id,
        User.is_deleted == False
    )
    res = await db.execute(stmt)
    user = res.scalar_one_or_none()
    if not user:
        raise HTTPException(status_code=404, detail="Usuario no encontrado.")

    if data.full_name is not None:
        user.full_name = data.full_name.strip()
    if data.email is not None:
        user.email = data.email.strip()
    if data.phone is not None:
        user.phone = data.phone.strip()
    if data.is_active is not None:
        user.is_active = data.is_active

    if data.current_branch_id is not None:
        if data.current_branch_id == "":
            user.current_branch_id = None
        else:
            try:
                user.current_branch_id = uuid.UUID(data.current_branch_id)
            except ValueError:
                pass

    st = user.settings or {}
    if data.supervisor_pin is not None:
        if data.supervisor_pin.strip():
            st["supervisor_pin"] = data.supervisor_pin.strip()
        else:
            st.pop("supervisor_pin", None)
        user.settings = dict(st)

    # Actualizar Rol
    if data.role_code is not None:
        user.is_superuser = (data.role_code == "admin")
        await ensure_roles_seeded(db)
        role_stmt = select(Role).where(Role.code == data.role_code)
        role_res = await db.execute(role_stmt)
        role = role_res.scalar_one_or_none()
        if role:
            user.roles = [role]

    await db.commit()
    return {"id": str(user.id), "status": "updated", "message": "Usuario actualizado exitosamente."}


@router.post("/users/{user_id}/toggle-status")
async def toggle_user_status(
    user_id: str,
    db: AsyncSession = Depends(get_db),
    current_user=Depends(get_current_user)
):
    """Activar o suspender el acceso de un colaborador."""
    try:
        u_uuid = uuid.UUID(user_id)
    except ValueError:
        raise HTTPException(status_code=400, detail="ID de usuario inválido.")

    if u_uuid == current_user.id:
        raise HTTPException(status_code=400, detail="No puedes suspender tu propia cuenta activa.")

    stmt = select(User).where(
        User.id == u_uuid,
        User.company_id == current_user.company_id,
        User.is_deleted == False
    )
    res = await db.execute(stmt)
    user = res.scalar_one_or_none()
    if not user:
        raise HTTPException(status_code=404, detail="Usuario no encontrado.")

    user.is_active = not user.is_active
    await db.commit()

    return {
        "id": str(user.id),
        "is_active": user.is_active,
        "message": f"Usuario {'activado' if user.is_active else 'suspendido'} exitosamente."
    }


@router.post("/users/{user_id}/reset-password")
async def reset_user_password(
    user_id: str,
    data: ResetPasswordRequest,
    db: AsyncSession = Depends(get_db),
    current_user=Depends(get_current_user)
):
    """Restablecer la contraseña de un usuario."""
    if len(data.new_password) < 4:
        raise HTTPException(status_code=400, detail="La contraseña debe tener al menos 4 caracteres.")

    try:
        u_uuid = uuid.UUID(user_id)
    except ValueError:
        raise HTTPException(status_code=400, detail="ID de usuario inválido.")

    stmt = select(User).where(
        User.id == u_uuid,
        User.company_id == current_user.company_id,
        User.is_deleted == False
    )
    res = await db.execute(stmt)
    user = res.scalar_one_or_none()
    if not user:
        raise HTTPException(status_code=404, detail="Usuario no encontrado.")

    user.hashed_password = get_password_hash(data.new_password)
    await db.commit()

    return {"id": str(user.id), "message": "Contraseña restablecida exitosamente."}
