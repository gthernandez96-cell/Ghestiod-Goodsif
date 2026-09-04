"""API v1 — Clientes y Proveedores."""
from fastapi import APIRouter, Depends, HTTPException, Query, UploadFile, File
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy import select, or_, func
from pydantic import BaseModel
from typing import Optional, List
from decimal import Decimal
import uuid

from app.core.database import get_db
from app.core.security import get_current_user
from app.models.partner import Partner, PartnerType, CreditStatus
from app.models.sale import SaleOrder
from app.services.fel_service import lookup_nit_sat

router = APIRouter(prefix="/partners")


def check_partner_management_permission(current_user):
    """Verifica que el usuario no sea solo un cajero sin permisos de gestión de clientes/proveedores."""
    if getattr(current_user, "is_superuser", False) or getattr(current_user, "is_admin", False) or getattr(current_user, "username", "") == "admin" or (getattr(current_user, "email", "") and "admin" in getattr(current_user, "email", "")):
        return
    role_codes = [r.code for r in current_user.roles] if hasattr(current_user, "roles") and current_user.roles else []
    if any(code in role_codes for code in ["admin", "store_manager", "accountant", "inventory_manager"]):
        return
    if "cashier" in role_codes or not role_codes:
        raise HTTPException(
            status_code=403,
            detail="Acceso denegado: El usuario con rol Cajero no tiene autorización para crear o modificar fichas de clientes."
        )



class PartnerCreateIn(BaseModel):
    name: str
    nit: Optional[str] = "CF"
    cui: Optional[str] = None
    trade_name: Optional[str] = None
    partner_type: str = "customer"
    email: Optional[str] = None
    phone: Optional[str] = None
    mobile: Optional[str] = None
    address: Optional[str] = None
    city: Optional[str] = "Guatemala"
    country: Optional[str] = "Guatemala"
    is_company: bool = False
    credit_status: Optional[str] = "active"
    credit_limit: Decimal = Decimal("0")
    payment_days: int = 0
    image_url: Optional[str] = None
    notes: Optional[str] = None
    tags: Optional[List[str]] = None


class PartnerUpdateIn(BaseModel):
    name: Optional[str] = None
    nit: Optional[str] = None
    cui: Optional[str] = None
    trade_name: Optional[str] = None
    partner_type: Optional[str] = None
    email: Optional[str] = None
    phone: Optional[str] = None
    mobile: Optional[str] = None
    address: Optional[str] = None
    city: Optional[str] = None
    country: Optional[str] = None
    is_company: Optional[bool] = None
    credit_status: Optional[str] = None
    credit_limit: Optional[Decimal] = None
    payment_days: Optional[int] = None
    image_url: Optional[str] = None
    is_active: Optional[bool] = None
    notes: Optional[str] = None
    tags: Optional[List[str]] = None


class CreditPaymentIn(BaseModel):
    amount: Decimal
    payment_method: str = "cash"
    reference: Optional[str] = None
    notes: Optional[str] = None


@router.get("/")
async def list_partners(
    search: Optional[str] = Query(None),
    partner_type: Optional[str] = Query(None),
    is_active: Optional[bool] = Query(True),
    db: AsyncSession = Depends(get_db),
    current_user=Depends(get_current_user),
):
    """Listar y buscar clientes o proveedores."""
    stmt = select(Partner).where(Partner.company_id == current_user.company_id)

    if is_active is not None:
        stmt = stmt.where(Partner.is_active == is_active)

    if partner_type:
        try:
            pt_enum = PartnerType(partner_type.lower())
            stmt = stmt.where(or_(Partner.partner_type == pt_enum, Partner.partner_type == PartnerType.BOTH))
        except ValueError:
            pass

    if search:
        pattern = f"%{search}%"
        stmt = stmt.where(
            or_(
                Partner.name.ilike(pattern),
                Partner.nit.ilike(pattern),
                Partner.phone.ilike(pattern),
                Partner.email.ilike(pattern),
            )
        )

    stmt = stmt.order_by(Partner.name)
    res = await db.execute(stmt)
    partners = res.scalars().all()

    items = [
        {
            "id": str(p.id),
            "name": p.name,
            "nit": p.nit or "CF",
            "cui": p.cui or "",
            "trade_name": p.trade_name or "",
            "partner_type": p.partner_type.value if hasattr(p.partner_type, 'value') else str(p.partner_type),
            "email": p.email or "",
            "phone": p.phone or "",
            "mobile": p.mobile or "",
            "address": p.address or "",
            "city": p.city or "Guatemala",
            "credit_limit": float(p.credit_limit or 0),
            "payment_days": p.payment_days or 0,
            "credit_status": p.credit_status.value if hasattr(p.credit_status, 'value') else str(p.credit_status),
            "balance": float(p.balance or 0),
            "image_url": p.image_url or "",
            "is_active": p.is_active,
        }
        for p in partners
    ]

    return {"items": items, "total": len(items)}


@router.get("/lookup-nit/{nit}")
async def lookup_partner_by_nit(
    nit: str,
    db: AsyncSession = Depends(get_db),
    current_user=Depends(get_current_user),
):
    """
    Buscar un cliente por NIT para facturación rápida en POS y emisión FEL.
    1. Si es CF: retorna Consumidor Final de inmediato.
    2. Si existe en la base de datos local de la empresa: retorna en 0ms.
    3. Si no existe: consulta en tiempo real ante la SAT / Certificador FEL con validación de Módulo 11.
    """
    nit_raw = nit.strip()
    nit_clean = nit_raw.replace("-", "").replace(" ", "").upper()

    # 1. Consumidor Final
    if nit_clean in ("CF", "CONSUMIDORFINAL", "C/F") or not nit_clean:
        return {
            "found": True,
            "valid": True,
            "nit": "CF",
            "name": "CONSUMIDOR FINAL",
            "trade_name": "Consumidor Final",
            "email": "",
            "address": "CIUDAD DE GUATEMALA",
            "source": "cf",
            "items": [{"nit": "CF", "name": "CONSUMIDOR FINAL", "email": "", "address": "CIUDAD DE GUATEMALA"}]
        }

    # 2. Búsqueda en Base de Datos Local de la Empresa (Caché ultrarrápida)
    stmt = select(Partner).where(
        Partner.company_id == current_user.company_id,
        or_(
            Partner.nit.ilike(nit_raw),
            Partner.nit.ilike(nit_clean),
            func.replace(Partner.nit, "-", "").ilike(nit_clean)
        )
    ).limit(1)
    res = await db.execute(stmt)
    partner = res.scalar_one_or_none()

    if partner:
        return {
            "found": True,
            "valid": True,
            "nit": partner.nit,
            "name": partner.name,
            "trade_name": partner.trade_name or partner.name,
            "email": partner.email or "",
            "address": partner.address or "CIUDAD DE GUATEMALA",
            "source": "local_database",
            "items": [{
                "id": str(partner.id),
                "nit": partner.nit,
                "name": partner.name,
                "email": partner.email or "",
                "address": partner.address or "CIUDAD DE GUATEMALA",
            }]
        }

    # 3. Consulta en tiempo real con algoritmo Módulo 11 SAT y Certificador FEL
    sat_result = await lookup_nit_sat(nit_raw)
    if sat_result.get("valid"):
        formatted_nit = sat_result.get("nit") or nit_raw
        fiscal_name = sat_result.get("name")
        found = bool(fiscal_name)
        address = sat_result.get("address") or "CIUDAD DE GUATEMALA"
        trade_name = sat_result.get("trade_name") or fiscal_name

        return {
            "found": found,
            "valid": True,
            "nit": formatted_nit,
            "name": fiscal_name,
            "trade_name": trade_name,
            "email": "",
            "address": address,
            "source": sat_result.get("source", "sat_api"),
            "message": sat_result.get("message"),
            "items": [{
                "id": None,
                "nit": formatted_nit,
                "name": fiscal_name,
                "email": "",
                "address": address
            }] if fiscal_name else []
        }

    # 4. NIT no superó la validación oficial de Módulo 11 de la SAT
    return {
        "found": False,
        "valid": False,
        "nit": sat_result.get("nit") or nit_raw,
        "name": None,
        "trade_name": None,
        "email": "",
        "address": None,
        "error": sat_result.get("error") or "NIT inválido según algoritmo oficial de SAT",
        "source": "sat_modulo11_failed",
        "items": []
    }


@router.get("/credits")
async def get_customer_credits(
    search: Optional[str] = Query(None),
    db: AsyncSession = Depends(get_db),
    current_user=Depends(get_current_user),
):
    """Obtener resumen de cuentas por cobrar y créditos de clientes."""
    stmt = select(Partner).where(
        Partner.company_id == current_user.company_id,
        Partner.is_active == True,
        or_(Partner.credit_limit > 0, Partner.balance > 0)
    )
    if search:
        s = f"%{search}%"
        stmt = stmt.where(or_(Partner.name.ilike(s), Partner.nit.ilike(s), Partner.phone.ilike(s)))

    res = await db.execute(stmt)
    partners = res.scalars().all()

    items = []
    total_credit_limit = Decimal("0")
    total_debt = Decimal("0")

    for p in partners:
        c_limit = Decimal(str(p.credit_limit or 0))
        c_balance = Decimal(str(p.balance or 0))
        c_available = max(Decimal("0"), c_limit - c_balance)

        total_credit_limit += c_limit
        total_debt += c_balance

        items.append({
            "id": str(p.id),
            "name": p.name,
            "nit": p.nit,
            "phone": p.phone,
            "email": p.email,
            "credit_limit": float(c_limit),
            "balance": float(c_balance),
            "available_credit": float(c_available),
            "payment_days": p.payment_days or 30,
            "credit_status": p.credit_status.value if hasattr(p.credit_status, "value") else str(p.credit_status)
        })

    return {
        "items": items,
        "total_credit_limit": float(total_credit_limit),
        "total_debt": float(total_debt),
        "total_available": float(max(Decimal("0"), total_credit_limit - total_debt))
    }


@router.get("/kpis")
async def get_partners_kpis(
    db: AsyncSession = Depends(get_db),
    current_user=Depends(get_current_user),
):
    """Obtener métricas y KPIs ejecutivos de la cartera de clientes y proveedores."""
    stmt = select(Partner).where(Partner.company_id == current_user.company_id, Partner.is_active == True)
    res = await db.execute(stmt)
    partners = res.scalars().all()

    total_customers = sum(1 for p in partners if p.partner_type in (PartnerType.CUSTOMER, PartnerType.BOTH))
    total_suppliers = sum(1 for p in partners if p.partner_type in (PartnerType.SUPPLIER, PartnerType.BOTH))
    total_debt = sum(Decimal(str(p.balance or 0)) for p in partners if (p.balance or 0) > 0)
    total_credit_limit = sum(Decimal(str(p.credit_limit or 0)) for p in partners)
    debtor_customers_count = sum(1 for p in partners if (p.balance or 0) > 0)

    return {
        "total_contacts": len(partners),
        "total_customers": total_customers,
        "total_suppliers": total_suppliers,
        "total_debt": float(total_debt),
        "total_credit_limit": float(total_credit_limit),
        "total_available_credit": float(max(Decimal("0"), total_credit_limit - total_debt)),
        "debtor_customers_count": debtor_customers_count,
    }


# ===== IMPORTACIÓN / EXPORTACIÓN CSV DE CLIENTES / PROVEEDORES =====
@router.get("/template-csv")
async def download_partner_csv_template():
    """Descargar plantilla CSV para importación masiva de clientes y proveedores."""
    import csv
    import io
    from fastapi.responses import Response

    output = io.StringIO()
    writer = csv.writer(output, delimiter=",")
    writer.writerow([
        "nombre", "nit", "cui", "tipo", "telefono",
        "email", "direccion", "limite_credito", "dias_credito"
    ])
    writer.writerow([
        "Juan Carlos Pérez", "1234567-8", "2500123450101", "customer", "55551234",
        "juan@ejemplo.com", "Zona 10, Ciudad de Guatemala", "5000.00", "30"
    ])
    writer.writerow([
        "Distribuidora El Éxito S.A.", "8765432-1", "", "supplier", "22223333",
        "ventas@elexito.com", "Calzada Roosevelt, Mixco", "0.00", "0"
    ])

    csv_data = "\ufeff" + output.getvalue()
    return Response(
        content=csv_data,
        media_type="text/csv",
        headers={"Content-Disposition": 'attachment; filename="plantilla_clientes_proveedores.csv"'}
    )


@router.post("/import-csv")
async def import_partners_csv(
    file: UploadFile = File(...),
    partner_type: str = Query("customer"),
    db: AsyncSession = Depends(get_db),
    current_user=Depends(get_current_user),
):
    """Importar clientes o proveedores masivamente desde archivo CSV."""
    check_partner_management_permission(current_user)
    import csv
    import io

    content = await file.read()
    text = content.decode("utf-8-sig", errors="replace")
    csv_reader = csv.DictReader(io.StringIO(text))

    if not csv_reader.fieldnames:
        raise HTTPException(status_code=400, detail="El archivo CSV está vacío o no es válido.")

    def clean_col(s):
        return s.strip().lower().replace(" ", "_").replace("á", "a").replace("é", "e").replace("í", "i").replace("ó", "o").replace("ú", "u")

    created_count = 0
    updated_count = 0
    errors = []

    for idx, raw_row in enumerate(csv_reader, start=2):
        row = {clean_col(k): v.strip() for k, v in raw_row.items() if k}
        name = row.get("nombre") or row.get("name") or row.get("razon_social")
        if not name:
            errors.append(f"Fila {idx}: Nombre es obligatorio.")
            continue

        nit = row.get("nit") or "CF"
        cui = row.get("cui") or row.get("dpi") or None
        partner_type_str = (row.get("tipo") or row.get("type") or "customer").lower()
        partner_type = PartnerType.SUPPLIER if "prov" in partner_type_str or "sup" in partner_type_str else PartnerType.CUSTOMER

        phone = row.get("telefono") or row.get("phone") or row.get("celular") or None
        email = row.get("email") or row.get("correo") or None
        address = row.get("direccion") or row.get("address") or None

        try:
            credit_limit = Decimal(str(row.get("limite_credito") or "0").replace(",", "."))
        except Exception:
            credit_limit = Decimal("0")

        try:
            payment_days = int(row.get("dias_credito") or 0)
        except Exception:
            payment_days = 0

        existing = None
        if nit and nit.upper() != "CF":
            stmt = select(Partner).where(
                Partner.company_id == current_user.company_id,
                Partner.nit == nit
            )
            res = await db.execute(stmt)
            existing = res.scalars().first()

        if existing:
            existing.name = name
            if cui:
                existing.cui = cui
            if phone:
                existing.phone = phone
            if email:
                existing.email = email
            if address:
                existing.address = address
            if credit_limit > 0:
                existing.credit_limit = credit_limit
            if payment_days > 0:
                existing.payment_days = payment_days
            updated_count += 1
        else:
            partner_obj = Partner(
                company_id=current_user.company_id,
                name=name,
                nit=nit,
                cui=cui,
                partner_type=partner_type,
                phone=phone,
                email=email,
                address=address,
                credit_limit=credit_limit,
                payment_days=payment_days,
                is_active=True
            )
            db.add(partner_obj)
            created_count += 1

    await db.commit()
    return {
        "status": "success",
        "created": created_count,
        "updated": updated_count,
        "errors": errors,
        "total_processed": created_count + updated_count
    }


@router.get("/export-csv")
async def export_partners_csv(
    partner_type: Optional[str] = None,
    db: AsyncSession = Depends(get_db),
    current_user=Depends(get_current_user),
):
    """Exportar listado de clientes y/o proveedores a CSV."""
    import csv
    import io
    from fastapi.responses import Response

    stmt = select(Partner).where(
        Partner.company_id == current_user.company_id,
        Partner.is_active == True
    )
    if partner_type:
        stmt = stmt.where(Partner.partner_type == partner_type)

    stmt = stmt.order_by(Partner.name)
    res = await db.execute(stmt)
    partners = res.scalars().all()

    output = io.StringIO()
    writer = csv.writer(output, delimiter=",")
    writer.writerow([
        "ID", "Nombre", "NIT", "CUI", "Tipo",
        "Teléfono", "Email", "Dirección", "Límite Crédito (Q)", "Saldo Deuda (Q)", "Días Crédito"
    ])

    for p in partners:
        p_type = "Cliente" if p.partner_type == PartnerType.CUSTOMER else "Proveedor"
        writer.writerow([
            str(p.id),
            p.name,
            p.nit or "CF",
            p.cui or "",
            p_type,
            p.phone or "",
            p.email or "",
            p.address or "",
            f"{float(p.credit_limit or 0):.2f}",
            f"{float(p.balance or 0):.2f}",
            p.payment_days or 0
        ])

    csv_data = "\ufeff" + output.getvalue()
    return Response(
        content=csv_data,
        media_type="text/csv",
        headers={"Content-Disposition": 'attachment; filename="socios_comerciales.csv"'}
    )


@router.get("/{partner_id}")
async def get_partner(
    partner_id: str,
    db: AsyncSession = Depends(get_db),
    current_user=Depends(get_current_user),
):
    """Obtener detalle de un cliente/proveedor."""
    try:
        p_uuid = uuid.UUID(partner_id)
    except ValueError:
        raise HTTPException(status_code=400, detail="ID de socio inválido")

    stmt = select(Partner).where(Partner.id == p_uuid, Partner.company_id == current_user.company_id)
    res = await db.execute(stmt)
    p = res.scalar_one_or_none()

    if not p:
        raise HTTPException(status_code=404, detail="Socio no encontrado")

    c_limit = Decimal(str(p.credit_limit or 0))
    c_balance = Decimal(str(p.balance or 0))

    return {
        "id": str(p.id),
        "name": p.name,
        "nit": p.nit,
        "cui": p.cui,
        "trade_name": p.trade_name,
        "partner_type": p.partner_type.value if hasattr(p.partner_type, 'value') else str(p.partner_type),
        "email": p.email,
        "phone": p.phone,
        "mobile": p.mobile,
        "address": p.address,
        "city": p.city,
        "country": p.country or "Guatemala",
        "is_company": p.is_company,
        "credit_status": p.credit_status.value if hasattr(p.credit_status, 'value') else str(p.credit_status),
        "credit_limit": float(c_limit),
        "balance": float(c_balance),
        "available_credit": float(max(Decimal("0"), c_limit - c_balance)),
        "payment_days": p.payment_days or 0,
        "image_url": p.image_url or "",
        "is_active": p.is_active,
        "notes": p.notes,
        "tags": p.tags or [],
    }


@router.get("/{partner_id}/sales")
async def get_partner_sales_history(
    partner_id: str,
    limit: int = Query(20, ge=1, le=100),
    db: AsyncSession = Depends(get_db),
    current_user=Depends(get_current_user),
):
    """Obtener historial de compras del cliente."""
    try:
        p_uuid = uuid.UUID(partner_id)
    except ValueError:
        raise HTTPException(status_code=400, detail="ID de socio inválido")

    stmt = select(SaleOrder).where(
        SaleOrder.customer_id == p_uuid,
        SaleOrder.company_id == current_user.company_id
    ).order_by(SaleOrder.created_at.desc()).limit(limit)

    res = await db.execute(stmt)
    sales = res.scalars().all()

    items = []
    for s in sales:
        items.append({
            "id": str(s.id),
            "number": s.number,
            "sale_date": s.sale_date.strftime("%d/%m/%Y %H:%M") if s.sale_date else s.created_at.strftime("%d/%m/%Y %H:%M"),
            "total": float(s.total or 0),
            "amount_paid": float(s.amount_paid or 0),
            "amount_due": float(s.amount_due or 0),
            "state": s.state.value if hasattr(s.state, "value") else str(s.state),
            "fel_uuid": s.fel_uuid,
            "fel_number": s.fel_number,
        })
    return {"items": items, "total": len(items)}


@router.get("/{partner_id}/statement")
async def get_partner_statement(
    partner_id: str,
    db: AsyncSession = Depends(get_db),
    current_user=Depends(get_current_user),
):
    """Generar estado de cuenta consolidado del cliente."""
    try:
        p_uuid = uuid.UUID(partner_id)
    except ValueError:
        raise HTTPException(status_code=400, detail="ID de socio inválido")

    stmt = select(Partner).where(Partner.id == p_uuid, Partner.company_id == current_user.company_id)
    res = await db.execute(stmt)
    p = res.scalar_one_or_none()
    if not p:
        raise HTTPException(status_code=404, detail="Socio no encontrado")

    sales_stmt = select(SaleOrder).where(
        SaleOrder.customer_id == p_uuid,
        SaleOrder.company_id == current_user.company_id
    ).order_by(SaleOrder.created_at.desc()).limit(50)
    sales_res = await db.execute(sales_stmt)
    sales = sales_res.scalars().all()

    c_limit = Decimal(str(p.credit_limit or 0))
    c_balance = Decimal(str(p.balance or 0))

    return {
        "customer": {
            "id": str(p.id),
            "name": p.name,
            "trade_name": p.trade_name,
            "nit": p.nit,
            "cui": p.cui,
            "phone": p.phone or p.mobile,
            "email": p.email,
            "address": p.address,
            "city": p.city,
            "credit_limit": float(c_limit),
            "balance": float(c_balance),
            "available_credit": float(max(Decimal("0"), c_limit - c_balance)),
            "payment_days": p.payment_days or 30,
            "credit_status": p.credit_status.value if hasattr(p.credit_status, "value") else str(p.credit_status),
        },
        "sales": [
            {
                "id": str(s.id),
                "number": s.number,
                "date": s.sale_date.strftime("%d/%m/%Y %H:%M") if s.sale_date else s.created_at.strftime("%d/%m/%Y %H:%M"),
                "total": float(s.total or 0),
                "amount_paid": float(s.amount_paid or 0),
                "amount_due": float(s.amount_due or 0),
                "state": s.state.value if hasattr(s.state, "value") else str(s.state),
                "fel_uuid": s.fel_uuid,
            }
            for s in sales
        ],
        "summary": {
            "total_purchases": sum(float(s.total or 0) for s in sales),
            "total_pending_due": float(c_balance),
            "credit_utilization_pct": float(min(Decimal("100"), (c_balance / c_limit * 100))) if c_limit > 0 else 0.0,
        }
    }


@router.post("/")
async def create_partner(
    data: PartnerCreateIn,
    db: AsyncSession = Depends(get_db),
    current_user=Depends(get_current_user),
):
    """Crear un cliente o proveedor."""
    check_partner_management_permission(current_user)
    try:
        pt_enum = PartnerType(data.partner_type.lower())
    except ValueError:
        pt_enum = PartnerType.CUSTOMER

    try:
        cs_enum = CreditStatus(data.credit_status.lower()) if data.credit_status else CreditStatus.ACTIVE
    except ValueError:
        cs_enum = CreditStatus.ACTIVE

    partner = Partner(
        company_id=current_user.company_id,
        name=data.name,
        nit=data.nit or "CF",
        cui=data.cui,
        trade_name=data.trade_name,
        partner_type=pt_enum,
        email=data.email,
        phone=data.phone,
        mobile=data.mobile,
        address=data.address,
        city=data.city or "Guatemala",
        country=data.country or "Guatemala",
        is_company=data.is_company,
        credit_status=cs_enum,
        credit_limit=data.credit_limit,
        payment_days=data.payment_days,
        image_url=data.image_url,
        notes=data.notes,
        tags=data.tags or [],
    )
    db.add(partner)
    await db.commit()
    await db.refresh(partner)

    return {"id": str(partner.id), "name": partner.name, "nit": partner.nit, "status": "created"}


@router.put("/{partner_id}")
async def update_partner(
    partner_id: str,
    data: PartnerUpdateIn,
    db: AsyncSession = Depends(get_db),
    current_user=Depends(get_current_user),
):
    """Actualizar datos de cliente/proveedor."""
    check_partner_management_permission(current_user)
    try:
        p_uuid = uuid.UUID(partner_id)
    except ValueError:
        raise HTTPException(status_code=400, detail="ID de socio inválido")

    stmt = select(Partner).where(Partner.id == p_uuid, Partner.company_id == current_user.company_id)
    res = await db.execute(stmt)
    p = res.scalar_one_or_none()

    if not p:
        raise HTTPException(status_code=404, detail="Socio no encontrado")

    if data.name is not None: p.name = data.name
    if data.nit is not None: p.nit = data.nit
    if data.cui is not None: p.cui = data.cui
    if data.trade_name is not None: p.trade_name = data.trade_name
    if data.partner_type is not None:
        try: p.partner_type = PartnerType(data.partner_type.lower())
        except ValueError: pass
    if data.email is not None: p.email = data.email
    if data.phone is not None: p.phone = data.phone
    if data.mobile is not None: p.mobile = data.mobile
    if data.address is not None: p.address = data.address
    if data.city is not None: p.city = data.city
    if data.country is not None: p.country = data.country
    if data.is_company is not None: p.is_company = data.is_company
    if data.credit_status is not None:
        try: p.credit_status = CreditStatus(data.credit_status.lower())
        except ValueError: pass
    if data.credit_limit is not None: p.credit_limit = data.credit_limit
    if data.payment_days is not None: p.payment_days = data.payment_days
    if data.image_url is not None: p.image_url = data.image_url
    if data.is_active is not None: p.is_active = data.is_active
    if data.notes is not None: p.notes = data.notes
    if data.tags is not None: p.tags = data.tags

    await db.commit()
    return {"id": str(p.id), "status": "updated"}


@router.post("/{partner_id}/credit-payments")
async def pay_customer_credit(
    partner_id: str,
    data: CreditPaymentIn,
    db: AsyncSession = Depends(get_db),
    current_user=Depends(get_current_user),
):
    """Registrar un abono o pago a la deuda del cliente."""
    try:
        p_uuid = uuid.UUID(partner_id)
    except ValueError:
        raise HTTPException(status_code=400, detail="ID de socio inválido")

    stmt = select(Partner).where(Partner.id == p_uuid, Partner.company_id == current_user.company_id)
    res = await db.execute(stmt)
    partner = res.scalar_one_or_none()

    if not partner:
        raise HTTPException(status_code=404, detail="Cliente no encontrado")

    if data.amount <= 0:
        raise HTTPException(status_code=400, detail="El monto a abonar debe ser mayor a cero")

    current_balance = Decimal(str(partner.balance or 0))
    new_balance = max(Decimal("0"), current_balance - data.amount)
    partner.balance = new_balance

    await db.commit()

    credit_limit = Decimal(str(partner.credit_limit or 0))
    available = max(Decimal("0"), credit_limit - new_balance)

    return {
        "partner_id": str(partner.id),
        "partner_name": partner.name,
        "amount_paid": float(data.amount),
        "previous_balance": float(current_balance),
        "new_balance": float(new_balance),
        "available_credit": float(available),
        "status": "payment_registered"
    }


