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
        "description": "Gestión operativa integral: Punto de Venta, Pre-Venta, Solicitudes POS, Preparación en Bodega, Inventarios, Clientes, Cotizaciones, Cuentas por Cobrar, Cuentas por Pagar, Compras, Traslados, Reportes, Fabricación, Bodegas y Configuración de sucursal (Empresa/Logo, POS, Stock y Pagos). Bloqueado para Contabilidad general, FEL SAT, creación de empresas y gestión de usuarios.",
        "permissions": [
            {"module": "Ventas & Operaciones POS", "actions": "Cobro POS, Pre-Venta, Solicitudes POS, Preparación en Bodega, Autorizaciones PIN"},
            {"module": "Inventarios & Almacenes", "actions": "Catálogo Maestro, Existencias, Traslados, Fabricación, Bodegas"},
            {"module": "Clientes & Cotizaciones", "actions": "Directorio de Clientes, Límites de Crédito, Cotizaciones / Proformas"},
            {"module": "Finanzas & Compras", "actions": "Órdenes de Compra, Cuentas por Cobrar (AR), Cuentas por Pagar (AP)"},
            {"module": "Reportes & Analítica", "actions": "Reportes Operativos, Detalle de Ventas, Desempeño y Auditoría"},
            {"module": "Configuración de Sucursal", "actions": "Empresa & Logo, Punto de Venta & Caja, Inventarios y Métodos de Pago USD"},
            {"module": "Contabilidad General & FEL", "actions": "Sin acceso (Libro Diario, Balance, Partidas y FEL bloqueados)"},
            {"module": "Multi-Empresas & Usuarios", "actions": "Sin acceso (Creación de nuevas empresas, usuarios y roles bloqueados)"}
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
            {"module": "Créditos & AR", "actions": "Sin acceso (Bloqueado: Registro de abonos de crédito)"},
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
        "description": "Responsable de almacenes y bodegas. Módulos exclusivos: Preparación en Bodega, Inventario, Traslados y Fabricación. Bloqueado para ventas, cobros POS, pre-venta, cotizaciones, compras, clientes y finanzas.",
        "permissions": [
            {"module": "Preparación en Bodega", "actions": "Alistamiento, picking, verificación y despacho de pedidos"},
            {"module": "Inventarios", "actions": "Catálogo Maestro, Existencias por Bodega, Ajustes, Kardex y Lotes"},
            {"module": "Traslados", "actions": "Solicitud, emisión y recepción de traslados entre bodegas"},
            {"module": "Fabricación", "actions": "Órdenes de Ensamblaje, Fabricación y Recetas / BOM"},
            {"module": "POS & Pre-Venta", "actions": "Sin acceso (Cobros y ventas bloqueados)"},
            {"module": "Cotizaciones & Clientes", "actions": "Sin acceso"},
            {"module": "Compras & AP", "actions": "Sin acceso"},
            {"module": "Finanzas & Contabilidad", "actions": "Sin acceso (Cuentas por Cobrar/Pagar, Libro Diario, FEL)"},
            {"module": "Configuración & Usuarios", "actions": "Sin acceso"}
        ]
    },
    {
        "code": "accountant",
        "name": "Contador / Finanzas",
        "badge_color": "#9333ea",
        "badge_bg": "#f3e8ff",
        "icon": "ph-scales",
        "description": "Responsable de cartera, proveedores y analítica financiera. Acceso exclusivo a: Cuentas por Cobrar (AR), Cuentas por Pagar (AP) y Reportes SAT & BI. Bloqueado para caja POS, pre-venta, inventarios, contabilidad general, facturación FEL y configuración.",
        "permissions": [
            {"module": "Cuentas por Cobrar (AR)", "actions": "Control de Cartera, Estados de Cuenta, Facturas por Cobrar, Abonos"},
            {"module": "Cuentas por Pagar (AP)", "actions": "Facturas de Proveedores, Programación de Pagos, Liquidaciones"},
            {"module": "Reportes & BI", "actions": "Reportes SAT, Detalle de Ventas, Analítica de Negocio y Auditoría"},
            {"module": "Contabilidad & Facturación FEL", "actions": "Sin acceso (Libro Diario, Partidas y FEL bloqueados)"},
            {"module": "POS & Pre-Venta", "actions": "Sin acceso (Cobros en caja y toma de pedidos bloqueados)"},
            {"module": "Inventarios & Traslados", "actions": "Sin acceso (Catálogo y movimientos de bodega bloqueados)"},
            {"module": "Cotizaciones & Clientes", "actions": "Sin acceso"},
            {"module": "Configuración & Usuarios", "actions": "Sin acceso"}
        ]
    },
    {
        "code": "presale_seller",
        "name": "Pre-Vendedor",
        "badge_color": "#d97706",
        "badge_bg": "#fef3c7",
        "icon": "ph-lightning",
        "description": "Acceso exclusivo a Pre-Venta de pedidos, Consulta de Estados de Cuenta de Clientes y Emisión de Cotizaciones. Restricción estricta: Bloqueado para ver existencias de inventario de productos.",
        "permissions": [
            {"module": "Pre-Venta", "actions": "Toma de pedidos en ruta, catálogo de productos preventa, sincronización offline (Existencias de productos ocultas)"},
            {"module": "Cotizaciones", "actions": "Creación, consulta e impresión de proformas / cotizaciones (Existencias de stock ocultas)"},
            {"module": "Créditos & AR", "actions": "Consulta de Estados de Cuenta y saldos de clientes"},
            {"module": "Inventarios", "actions": "Sin acceso (Existencias de stock, catálogo maestro, costos y traslados bloqueados)"},
            {"module": "POS & Ventas", "actions": "Sin acceso (Cobro en mostrador bloqueado)"},
            {"module": "Configuración & Otros", "actions": "Sin acceso"}
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
    """Asegura que los roles predeterminados existan en la base de datos."""
    for r_data in PREDEFINED_ROLES:
        stmt = select(Role).where(
            or_(
                Role.code == r_data["code"],
                Role.code == "preventista" if r_data["code"] == "presale_seller" else False,
                Role.code == "warehouse" if r_data["code"] == "inventory_manager" else False,
                Role.code == "contador" if r_data["code"] == "accountant" else False
            )
        )
        res = await db.execute(stmt)
        role = res.scalars().first()
        if not role:
            role = Role(
                name=r_data["name"],
                code=r_data["code"],
                description=r_data["description"],
                is_system=True
            )
            db.add(role)
        elif r_data["code"] == "presale_seller":
            role.name = "Pre-Vendedor"
            role.description = r_data["description"]
        elif r_data["code"] == "inventory_manager":
            role.name = "Encargado de Inventario / Bodeguero"
            role.description = r_data["description"]
        elif r_data["code"] == "accountant":
            role.name = "Contador / Finanzas"
            role.description = r_data["description"]
        elif r_data["code"] == "store_manager":
            role.name = "Gerente de Sucursal / Supervisor"
            role.description = r_data["description"]
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
            "Pre-Venta",
            "Cotizaciones",
            "Créditos & AR",
            "POS & Ventas",
            "Inventarios",
            "Compras & AP",
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
    presale_count = 0

    for u in users:
        role_codes = [r.code for r in u.roles]
        if u.is_superuser or "admin" in role_codes:
            admins_count += 1
        elif "store_manager" in role_codes or "manager" in role_codes or "gerente" in role_codes or "supervisor" in role_codes:
            managers_count += 1
        elif "inventory_manager" in role_codes or "warehouse" in role_codes or "bodeguero" in role_codes:
            inventory_count += 1
        elif "accountant" in role_codes or "contador" in role_codes or "finance" in role_codes:
            accountants_count += 1
        elif "presale_seller" in role_codes or "preventista" in role_codes:
            presale_count += 1
        else:
            cashiers_count += 1

    return {
        "total_users": total_users,
        "active_users": active_users,
        "cashiers_count": cashiers_count,
        "admins_count": admins_count,
        "managers_count": managers_count,
        "inventory_count": inventory_count,
        "accountants_count": accountants_count,
        "presale_count": presale_count
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
        if role_code == "preventista":
            role_code = "presale_seller"
        elif role_code in ("warehouse", "bodeguero"):
            role_code = "inventory_manager"
        elif role_code in ("contador", "finance"):
            role_code = "accountant"
        
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
            "created_at": u.created_at.strftime("%d/%m/%Y") if u.created_at else "",
            "is_admin": bool(u.is_superuser or role_code == "admin" or u.username == "admin"),
            "can_view_stock": True if bool(u.is_superuser or role_code == "admin" or u.username == "admin") else st.get("can_view_stock", (st.get("stock_visibility", {})).get("can_view_stock", True)),
            "stock_pos": True if bool(u.is_superuser or role_code == "admin" or u.username == "admin") else (st.get("stock_visibility", {})).get("pos", st.get("stock_pos", True)),
            "stock_presale": True if bool(u.is_superuser or role_code == "admin" or u.username == "admin") else (st.get("stock_visibility", {})).get("presale", st.get("stock_presale", True)),
            "stock_quotes": True if bool(u.is_superuser or role_code == "admin" or u.username == "admin") else (st.get("stock_visibility", {})).get("quotes", st.get("stock_quotes", True))
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
    role_stmt = select(Role).where(
        or_(
            Role.code == data.role_code,
            Role.code == "store_manager" if data.role_code in ["store_manager", "manager", "gerente", "supervisor"] else False,
            Role.code == "presale_seller" if data.role_code in ["presale_seller", "preventista"] else False,
            Role.code == "preventista" if data.role_code in ["presale_seller", "preventista"] else False,
            Role.code == "inventory_manager" if data.role_code in ["inventory_manager", "warehouse", "bodeguero"] else False,
            Role.code == "warehouse" if data.role_code in ["inventory_manager", "warehouse", "bodeguero"] else False,
            Role.code == "accountant" if data.role_code in ["accountant", "contador", "finance"] else False,
            Role.code == "contador" if data.role_code in ["accountant", "contador", "finance"] else False
        )
    )
    role_res = await db.execute(role_stmt)
    role = role_res.scalars().first()
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


# ===== GESTIÓN DE VISIBILIDAD DE EXISTENCIAS (STOCK) =====
def is_admin_user(user) -> bool:
    """Verifica si el usuario es Administrador o Super Administrador."""
    if not user:
        return False
    role_codes = [r.code for r in getattr(user, "roles", [])] if hasattr(user, "roles") and user.roles else []
    return bool(
        getattr(user, "is_superuser", False)
        or getattr(user, "is_admin", False)
        or "admin" in role_codes
        or getattr(user, "username", "").lower() == "admin"
    )


class UserStockVisibilityItem(BaseModel):
    user_id: str
    can_view_stock: bool = True
    stock_pos: bool = True
    stock_presale: bool = True
    stock_quotes: bool = True


class UpdateStockVisibilityBatchRequest(BaseModel):
    users: List[UserStockVisibilityItem]


@router.get("/users/stock-visibility")
async def get_users_stock_visibility(
    db: AsyncSession = Depends(get_db),
    current_user=Depends(get_current_user)
):
    """Listar la configuración de visibilidad de stock de todos los colaboradores.
    Exclusivo para el usuario Administrador."""
    if not is_admin_user(current_user):
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Acceso restringido: Únicamente el usuario Administrador puede consultar o configurar la visibilidad de existencias."
        )

    c_uuid = uuid.UUID(str(current_user.company_id))

    # Cargar sucursales para nombres
    b_stmt = select(Branch).where(Branch.company_id == c_uuid, Branch.is_deleted == False)
    b_res = await db.execute(b_stmt)
    branches = {b.id: b.name for b in b_res.scalars().all()}

    stmt = (
        select(User)
        .options(selectinload(User.roles))
        .where(
            User.company_id == c_uuid,
            User.is_deleted == False
        )
        .order_by(User.full_name)
    )
    res = await db.execute(stmt)
    users = res.scalars().all()

    items = []
    for u in users:
        role_obj = u.roles[0] if u.roles else None
        role_code = "admin" if u.is_superuser else (role_obj.code if role_obj else "cashier")
        if role_code == "preventista":
            role_code = "presale_seller"
        elif role_code in ("warehouse", "bodeguero"):
            role_code = "inventory_manager"
        elif role_code in ("contador", "finance"):
            role_code = "accountant"

        role_meta = next((r for r in PREDEFINED_ROLES if r["code"] == role_code), {
            "name": "Super Administrador" if u.is_superuser else "Colaborador",
            "badge_color": "#714B67",
            "badge_bg": "#f4eff3"
        })

        st = u.settings or {}
        stock_cfg = st.get("stock_visibility", {})
        
        is_u_admin = bool(u.is_superuser or role_code == "admin" or u.username == "admin")
        
        # Para administradores siempre está visible
        if is_u_admin:
            can_view = True
            pos_vis = True
            presale_vis = True
            quotes_vis = True
        else:
            can_view = st.get("can_view_stock", stock_cfg.get("can_view_stock", True))
            pos_vis = stock_cfg.get("pos", st.get("stock_pos", True))
            presale_vis = stock_cfg.get("presale", st.get("stock_presale", True))
            quotes_vis = stock_cfg.get("quotes", st.get("stock_quotes", True))

        items.append({
            "id": str(u.id),
            "username": u.username,
            "full_name": u.full_name,
            "email": u.email,
            "role_code": role_code,
            "role_name": role_meta["name"],
            "role_badge_color": role_meta["badge_color"],
            "role_badge_bg": role_meta["badge_bg"],
            "is_admin": is_u_admin,
            "branch_name": branches.get(u.current_branch_id, "Sucursal Principal"),
            "can_view_stock": can_view,
            "stock_pos": pos_vis,
            "stock_presale": presale_vis,
            "stock_quotes": quotes_vis
        })

    return items


@router.put("/users/stock-visibility")
async def update_users_stock_visibility(
    data: UpdateStockVisibilityBatchRequest,
    db: AsyncSession = Depends(get_db),
    current_user=Depends(get_current_user)
):
    """Guardar masivamente la visibilidad de stock para los usuarios.
    Exclusivo para el usuario Administrador."""
    if not is_admin_user(current_user):
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Acceso restringido: Únicamente el usuario Administrador puede modificar la visibilidad de existencias."
        )

    c_uuid = uuid.UUID(str(current_user.company_id))

    updated_count = 0
    for item in data.users:
        try:
            u_uuid = uuid.UUID(item.user_id)
        except ValueError:
            continue

        stmt = select(User).where(
            User.id == u_uuid,
            User.company_id == c_uuid,
            User.is_deleted == False
        )
        res = await db.execute(stmt)
        user = res.scalar_one_or_none()
        if not user:
            continue

        # No degradar visibilidad del superusuario admin principal
        if user.is_superuser or user.username == "admin":
            continue

        st = dict(user.settings or {})
        st["can_view_stock"] = bool(item.can_view_stock)
        st["stock_pos"] = bool(item.stock_pos)
        st["stock_presale"] = bool(item.stock_presale)
        st["stock_quotes"] = bool(item.stock_quotes)
        st["stock_visibility"] = {
            "can_view_stock": bool(item.can_view_stock),
            "pos": bool(item.stock_pos),
            "presale": bool(item.stock_presale),
            "quotes": bool(item.stock_quotes)
        }
        user.settings = st
        updated_count += 1

    await db.commit()
    return {
        "status": "ok",
        "updated_users": updated_count,
        "message": f"Permisos de visibilidad de stock actualizados para {updated_count} usuario(s)."
    }


@router.patch("/users/{user_id}/stock-visibility")
async def update_single_user_stock_visibility(
    user_id: str,
    data: UserStockVisibilityItem,
    db: AsyncSession = Depends(get_db),
    current_user=Depends(get_current_user)
):
    """Actualizar visibilidad de stock de un usuario individual.
    Exclusivo para el usuario Administrador."""
    if not is_admin_user(current_user):
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Acceso restringido: Únicamente el usuario Administrador puede modificar la visibilidad de existencias."
        )

    try:
        u_uuid = uuid.UUID(user_id)
    except ValueError:
        raise HTTPException(status_code=400, detail="ID de usuario inválido.")

    c_uuid = uuid.UUID(str(current_user.company_id))
    stmt = select(User).where(
        User.id == u_uuid,
        User.company_id == c_uuid,
        User.is_deleted == False
    )
    res = await db.execute(stmt)
    user = res.scalar_one_or_none()
    if not user:
        raise HTTPException(status_code=404, detail="Usuario no encontrado.")

    if user.is_superuser or user.username == "admin":
        return {
            "id": str(user.id),
            "can_view_stock": True,
            "message": "El Super Administrador siempre mantiene visibilidad total de existencias."
        }

    st = dict(user.settings or {})
    st["can_view_stock"] = bool(data.can_view_stock)
    st["stock_pos"] = bool(data.stock_pos)
    st["stock_presale"] = bool(data.stock_presale)
    st["stock_quotes"] = bool(data.stock_quotes)
    st["stock_visibility"] = {
        "can_view_stock": bool(data.can_view_stock),
        "pos": bool(data.stock_pos),
        "presale": bool(data.stock_presale),
        "quotes": bool(data.stock_quotes)
    }
    user.settings = st
    await db.commit()

    return {
        "id": str(user.id),
        "can_view_stock": data.can_view_stock,
        "stock_pos": data.stock_pos,
        "stock_presale": data.stock_presale,
        "stock_quotes": data.stock_quotes,
        "message": "Permisos de visibilidad actualizados exitosamente."
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
        role_stmt = select(Role).where(
            or_(
                Role.code == data.role_code,
                Role.code == "store_manager" if data.role_code in ["store_manager", "manager", "gerente", "supervisor"] else False,
                Role.code == "presale_seller" if data.role_code in ["presale_seller", "preventista"] else False,
                Role.code == "preventista" if data.role_code in ["presale_seller", "preventista"] else False,
                Role.code == "inventory_manager" if data.role_code in ["inventory_manager", "warehouse", "bodeguero"] else False,
                Role.code == "warehouse" if data.role_code in ["inventory_manager", "warehouse", "bodeguero"] else False,
                Role.code == "accountant" if data.role_code in ["accountant", "contador", "finance"] else False,
                Role.code == "contador" if data.role_code in ["accountant", "contador", "finance"] else False
            )
        )
        role_res = await db.execute(role_stmt)
        role = role_res.scalars().first()
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

