"""API v1 — Cotizaciones / Proformas Comerciales 360° — Productos Automotrices del Sur, S.A."""
from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy import select, and_, func, desc
from sqlalchemy.orm import selectinload
from pydantic import BaseModel, ConfigDict
from typing import Optional, List, Dict, Any
from decimal import Decimal
from datetime import datetime, timezone, timedelta
import uuid
import os
import smtplib
import urllib.parse
from email.mime.text import MIMEText
from email.mime.multipart import MIMEMultipart

from app.core.database import get_db
from app.core.security import get_current_user
from app.models.sale import SaleOrder, SaleOrderLine, SaleState, SaleType
from app.models.partner import Partner, PartnerType
from app.models.product import Product, UnitOfMeasure
from app.models.company import Branch, Warehouse, Company
from app.models.inventory import StockQuant, StockMove, StockMoveType
from app.models.user import User
from app.api.v1.ar import get_company_branding

router = APIRouter(prefix="/quotes")


@router.get("/branding/current")
async def get_current_quote_branding(
    db: AsyncSession = Depends(get_db),
    current_user=Depends(get_current_user),
):
    """Obtener el membrete y configuración gráfica de cotizaciones de la empresa activa."""
    c_uuid = uuid.UUID(str(current_user.company_id))
    stmt = select(Company).where(Company.id == c_uuid)
    res = await db.execute(stmt)
    c = res.scalar_one_or_none()
    return get_company_branding(c)


# ===== SCHEMAS =====
class QuoteLineIn(BaseModel):
    product_id: str
    product_name: Optional[str] = None
    product_code: Optional[str] = None
    uom_name: Optional[str] = None
    qty: float = 1.0
    unit_price: float
    discount_pct: float = 0.0


class QuoteCreateIn(BaseModel):
    customer_id: Optional[str] = None
    customer_code: Optional[str] = None
    customer_name: Optional[str] = "Consumidor Final"
    customer_nit: Optional[str] = "CF"
    customer_address: Optional[str] = ""
    customer_phone: Optional[str] = ""
    customer_company: Optional[str] = ""
    seller_name: Optional[str] = None
    condition: Optional[str] = "Contado"
    price_tier: Optional[str] = "standard"
    valid_days: int = 15
    notes: Optional[str] = None
    branch_id: Optional[str] = None
    warehouse_id: Optional[str] = None
    lines: List[QuoteLineIn] = []


class QuoteUpdateIn(BaseModel):
    customer_id: Optional[str] = None
    customer_code: Optional[str] = None
    customer_name: Optional[str] = None
    customer_nit: Optional[str] = None
    customer_address: Optional[str] = None
    customer_phone: Optional[str] = None
    customer_company: Optional[str] = None
    seller_name: Optional[str] = None
    condition: Optional[str] = None
    price_tier: Optional[str] = None
    valid_days: Optional[int] = 15
    notes: Optional[str] = None
    warehouse_id: Optional[str] = None
    lines: Optional[List[QuoteLineIn]] = None


class QuoteStateIn(BaseModel):
    state: str  # draft, sent, cancelled


class EmailQuoteIn(BaseModel):
    recipient_email: str
    subject: Optional[str] = None
    message: Optional[str] = None


# ===== HELPER PARA DETERMINAR ESTADO REAL =====
def _resolve_quote_state(quote: SaleOrder, now: datetime) -> str:
    """Calcula el estado dinámico (draft, sent, converted, expired, cancelled)."""
    if quote.state in (SaleState.CONFIRMED, SaleState.APPROVED):
        return "converted"
    if quote.state == SaleState.CANCELLED:
        return "cancelled"
    
    extra = quote.extra or {}
    if extra.get("state_custom") in ("confirmed", "converted"):
        return "converted"
    
    # Si la fecha de vencimiento ya pasó y sigue abierta, es expired
    if quote.due_date:
        due = quote.due_date
        if due.tzinfo is None:
            due = due.replace(tzinfo=timezone.utc)
        if due < now:
            return "expired"

    if extra.get("state_custom") == "sent" or quote.state == SaleState.PENDING_APPROVAL:
        return "sent"

    return "draft"


# ===== 1. KPIS =====
@router.get("/kpis")
async def get_quotes_kpis(
    db: AsyncSession = Depends(get_db),
    current_user=Depends(get_current_user),
):
    """KPIs del módulo de cotizaciones con estados ampliados."""
    c_uuid = uuid.UUID(str(current_user.company_id))
    now = datetime.now(timezone.utc)

    stmt = select(SaleOrder).where(
        SaleOrder.company_id == c_uuid,
        SaleOrder.sale_type == SaleType.QUOTE,
        SaleOrder.is_deleted == False
    )
    res = await db.execute(stmt)
    quotes = res.scalars().all()

    total_quotes = len(quotes)
    total_amount = 0.0
    draft_count = 0
    sent_count = 0
    converted_count = 0
    expired_count = 0

    for q in quotes:
        total_amount += float(q.total or 0.0)
        st = _resolve_quote_state(q, now)
        if st == "draft":
            draft_count += 1
        elif st == "sent":
            sent_count += 1
        elif st == "confirmed":
            converted_count += 1
        elif st == "expired":
            expired_count += 1

    return {
        "total_quotes": total_quotes,
        "total_amount": round(total_amount, 2),
        "draft_count": draft_count,
        "sent_count": sent_count,
        "converted_count": converted_count,
        "expired_count": expired_count,
    }


# ===== 2. LIST QUOTES =====
@router.get("/")
async def list_quotes(
    state: Optional[str] = Query(None),
    search: Optional[str] = Query(None),
    db: AsyncSession = Depends(get_db),
    current_user=Depends(get_current_user),
):
    """Listar cotizaciones con filtros y búsqueda completa."""
    c_uuid = uuid.UUID(str(current_user.company_id))
    now = datetime.now(timezone.utc)

    stmt = (
        select(SaleOrder)
        .options(
            selectinload(SaleOrder.customer),
            selectinload(SaleOrder.user)
        )
        .where(
            SaleOrder.company_id == c_uuid,
            SaleOrder.sale_type == SaleType.QUOTE,
            SaleOrder.is_deleted == False
        )
        .order_by(SaleOrder.created_at.desc())
    )

    res = await db.execute(stmt)
    quotes = res.scalars().all()

    items = []
    for q in quotes:
        st = _resolve_quote_state(q, now)

        # Filtro de estado
        if state and state != "all":
            if st != state:
                continue

        # Filtro de búsqueda
        if search:
            s_low = search.lower()
            q_num = (q.number or "").lower()
            c_name = (q.customer_name or "").lower()
            c_nit = (q.customer_nit or "").lower()
            extra = q.extra or {}
            c_comp = (extra.get("customer_company") or "").lower()
            if s_low not in q_num and s_low not in c_name and s_low not in c_nit and s_low not in c_comp:
                continue

        created_str = q.sale_date.strftime("%d/%m/%Y") if q.sale_date else ""
        due_str = q.due_date.strftime("%d/%m/%Y") if q.due_date else ""
        extra = q.extra or {}

        seller = extra.get("seller_name")
        if not seller and q.user:
            seller = getattr(q.user, "full_name", None) or getattr(q.user, "name", None)

        items.append({
            "id": str(q.id),
            "number": q.number,
            "date": created_str,
            "valid_until": due_str,
            "customer_name": q.customer_name or "Consumidor Final",
            "customer_company": extra.get("customer_company") or "",
            "customer_nit": q.customer_nit or "CF",
            "customer_phone": extra.get("customer_phone") or "",
            "condition": extra.get("condition") or "Contado",
            "price_tier": extra.get("price_tier") or "standard",
            "seller_name": seller or "Ventas",
            "subtotal": float(q.subtotal or 0),
            "tax_amount": float(q.tax_amount or 0),
            "discount_amount": float(q.discount_amount or 0),
            "total": float(q.total or 0),
            "state": st,
            "notes": q.notes or q.customer_address or ""
        })

    return items


# ===== 3. GET SINGLE QUOTE =====
@router.get("/{quote_id}")
async def get_quote(
    quote_id: str,
    db: AsyncSession = Depends(get_db),
    current_user=Depends(get_current_user),
):
    """Obtener detalle completo de una cotización con inventario, UOM y datos membretados."""
    try:
        q_uuid = uuid.UUID(quote_id)
    except ValueError:
        raise HTTPException(status_code=400, detail="ID de cotización inválido")

    stmt = (
        select(SaleOrder)
        .options(
            selectinload(SaleOrder.lines).selectinload(SaleOrderLine.product).selectinload(Product.uom),
            selectinload(SaleOrder.customer),
            selectinload(SaleOrder.branch),
            selectinload(SaleOrder.warehouse),
            selectinload(SaleOrder.user),
        )
        .where(
            SaleOrder.id == q_uuid,
            SaleOrder.company_id == current_user.company_id,
            SaleOrder.sale_type == SaleType.QUOTE
        )
    )
    res = await db.execute(stmt)
    quote = res.scalar_one_or_none()
    if not quote:
        raise HTTPException(status_code=404, detail="Cotización no encontrada")

    now = datetime.now(timezone.utc)
    st = _resolve_quote_state(quote, now)
    extra = quote.extra or {}

    # Obtener stock actual de cada producto en la bodega de la cotización
    wh_id = quote.warehouse_id
    product_ids = [l.product_id for l in quote.lines if l.product_id]
    stock_map = {}
    if product_ids and wh_id:
        sq_res = await db.execute(
            select(
                StockQuant.product_id,
                func.sum(StockQuant.qty_on_hand).label("stock")
            ).where(
                StockQuant.product_id.in_(product_ids),
                StockQuant.warehouse_id == wh_id,
                StockQuant.is_deleted == False
            ).group_by(StockQuant.product_id)
        )
        for p_id, stk in sq_res.all():
            stock_map[str(p_id)] = float(stk or 0.0)

    # Detalle de líneas
    lines_out = []
    for l in quote.lines:
        prod = l.product
        p_id_str = str(l.product_id)
        current_stock = stock_map.get(p_id_str, 0.0)
        
        # Determinar status de stock
        qty_num = float(l.qty)
        if current_stock >= qty_num:
            stock_status = "in_stock"
        elif current_stock > 0:
            stock_status = "partial"
        else:
            stock_status = "out_of_stock"

        # Medida (UOM)
        medida = "PZA"
        if prod and prod.uom:
            medida = prod.uom.symbol or prod.uom.name or "PZA"
        elif l.description and "MEDIDA:" in l.description:
            # fallback si se guardó en descripción
            try:
                medida = l.description.split("MEDIDA:")[1].split("|")[0].strip()
            except Exception:
                pass

        # Código de producto (SKU)
        code = ""
        if prod:
            code = prod.internal_ref or prod.barcode or ""
        if not code and l.product_barcode:
            code = l.product_barcode

        lines_out.append({
            "id": str(l.id),
            "product_id": p_id_str,
            "product_name": l.product_name,
            "product_code": code,
            "uom_name": medida.upper(),
            "qty": qty_num,
            "unit_price": float(l.unit_price),
            "cost_price": float(l.cost_price or 0.0),
            "discount_pct": float(l.discount_pct or 0.0),
            "discount_amount": float(l.discount_amount or 0.0),
            "subtotal": float(l.subtotal or 0.0),
            "tax_amount": float(l.tax_amount or 0.0),
            "total": float(l.total or 0.0),
            "current_stock": current_stock,
            "stock_status": stock_status,
        })

    # Datos de empresa membretada multi-empresa
    comp_res = await db.execute(select(Company).where(Company.id == quote.company_id))
    company = comp_res.scalar_one_or_none()
    comp_branding = get_company_branding(company)

    seller = extra.get("seller_name")
    if not seller and quote.user:
        seller = getattr(quote.user, "full_name", None) or getattr(quote.user, "name", None)

    cust_code = extra.get("customer_code")
    if not cust_code:
        if quote.customer and quote.customer.code:
            cust_code = f"#{quote.customer.code}"
        elif quote.customer_id:
            cust_code = f"#{str(quote.customer_id)[:6]}"
        else:
            cust_code = "CF"

    return {
        "id": str(quote.id),
        "number": quote.number,
        "date": quote.sale_date.strftime("%d/%m/%Y") if quote.sale_date else "",
        "date_iso": quote.sale_date.isoformat() if quote.sale_date else "",
        "valid_until": quote.due_date.strftime("%d/%m/%Y") if quote.due_date else "",
        "valid_days": int((quote.due_date - quote.sale_date).days) if (quote.due_date and quote.sale_date) else 15,
        "customer_id": str(quote.customer_id) if quote.customer_id else None,
        "customer_code": cust_code,
        "customer_name": quote.customer_name or "Consumidor Final",
        "customer_company": extra.get("customer_company") or (quote.customer.trade_name if quote.customer else "") or "",
        "customer_nit": quote.customer_nit or "CF",
        "customer_phone": extra.get("customer_phone") or (quote.customer.phone if quote.customer else "") or "",
        "customer_address": quote.customer_address or (quote.customer.address if quote.customer else "") or "",
        "customer_email": (quote.customer.email if quote.customer else "") or "",
        "condition": extra.get("condition") or "Contado",
        "price_tier": extra.get("price_tier") or "standard",
        "seller_name": seller or "Ventas",
        "branch_id": str(quote.branch_id),
        "branch_name": quote.branch.name if quote.branch else "Sucursal Principal",
        "warehouse_id": str(quote.warehouse_id),
        "warehouse_name": quote.warehouse.name if quote.warehouse else "Bodega Principal",
        "notes": quote.notes or "",
        "subtotal": float(quote.subtotal or 0),
        "discount_amount": float(quote.discount_amount or 0),
        "tax_amount": float(quote.tax_amount or 0),
        "total": float(quote.total or 0),
        "state": st,
        "is_expired": st == "expired",
        "lines": lines_out,
        "company": comp_branding,
        "company_info": {
            "name": comp_branding["title_name"] or comp_branding["name"],
            "location": comp_branding["address"],
            "nit": comp_branding["nit"],
            "phone": comp_branding["phone"],
            "emails": comp_branding["emails"],
            "logo_url": comp_branding["quote_logo"] or comp_branding["logo_url"],
            "quote_footer_image": comp_branding["quote_footer_image"],
            "is_official_goodsif": comp_branding["is_official_goodsif"],
            "contact_footer": comp_branding["contact_footer"]
        }
    }


# ===== 4. CREATE QUOTE =====
@router.post("/")
async def create_quote(
    data: QuoteCreateIn,
    db: AsyncSession = Depends(get_db),
    current_user=Depends(get_current_user),
):
    """Crear una nueva cotización/proforma formal con opciones 1 a 5."""
    c_uuid = uuid.UUID(str(current_user.company_id))

    # Resolver Branch y Warehouse
    b_uuid = None
    if data.branch_id:
        try:
            b_uuid = uuid.UUID(data.branch_id)
        except ValueError:
            pass
    if not b_uuid:
        b_res = await db.execute(select(Branch.id).where(Branch.company_id == c_uuid).limit(1))
        b_uuid = b_res.scalar_one_or_none()

    w_uuid = None
    if data.warehouse_id:
        try:
            w_uuid = uuid.UUID(data.warehouse_id)
        except ValueError:
            pass
    if not w_uuid:
        w_res = await db.execute(select(Warehouse.id).where(Warehouse.branch_id == b_uuid).limit(1))
        w_uuid = w_res.scalar_one_or_none()

    # Generar Secuencia única COT-XXXXX
    all_numbers_res = await db.execute(
        select(SaleOrder.number).where(SaleOrder.number.like("COT-%"))
    )
    existing_numbers = set(r[0] for r in all_numbers_res.all())

    max_seq = 0
    for num in existing_numbers:
        try:
            val = int(num.replace("COT-", ""))
            if val > max_seq:
                max_seq = val
        except (ValueError, TypeError):
            pass

    seq = max(max_seq + 1, 1)
    quote_number = f"COT-{seq:05d}"
    while quote_number in existing_numbers:
        seq += 1
        quote_number = f"COT-{seq:05d}"

    # Fechas
    now = datetime.now(timezone.utc)
    due_date = now + timedelta(days=data.valid_days or 15)

    # Cliente
    cust_uuid = None
    if data.customer_id:
        try:
            cust_uuid = uuid.UUID(data.customer_id)
        except ValueError:
            pass

    cust_code = data.customer_code
    if not cust_code and cust_uuid:
        c_res = await db.execute(select(Partner).where(Partner.id == cust_uuid))
        c_obj = c_res.scalar_one_or_none()
        if c_obj:
            cust_code = f"#{c_obj.code}" if c_obj.code else f"#{str(c_obj.id)[:6]}"
    if not cust_code:
        cust_code = "CF"

    seller = data.seller_name
    if not seller:
        seller = getattr(current_user, "full_name", None) or getattr(current_user, "name", None) or "Ventas"

    extra_data = {
        "condition": data.condition or "Contado",
        "price_tier": data.price_tier or "standard",
        "seller_name": seller,
        "customer_phone": data.customer_phone or "",
        "customer_company": data.customer_company or "",
        "customer_code": cust_code,
        "state_custom": "draft"
    }

    quote = SaleOrder(
        company_id=c_uuid,
        branch_id=b_uuid,
        warehouse_id=w_uuid,
        customer_id=cust_uuid,
        user_id=current_user.id,
        number=quote_number,
        state=SaleState.DRAFT,
        sale_type=SaleType.QUOTE,
        sale_date=now,
        due_date=due_date,
        customer_name=data.customer_name or "Consumidor Final",
        customer_nit=data.customer_nit or "CF",
        customer_address=data.customer_address or "",
        notes=data.notes or "",
        extra=extra_data
    )
    db.add(quote)
    await db.flush()

    total_subtotal = Decimal("0")
    total_tax = Decimal("0")
    total_disc = Decimal("0")
    total_final = Decimal("0")

    # Agregar líneas
    for line_in in data.lines:
        try:
            p_uuid = uuid.UUID(line_in.product_id)
        except ValueError:
            continue

        p_res = await db.execute(
            select(Product).options(selectinload(Product.uom)).where(Product.id == p_uuid)
        )
        prod = p_res.scalar_one_or_none()
        p_name = line_in.product_name or (prod.name if prod else "Artículo")
        c_price = prod.cost_price if prod else Decimal("0")
        barcode = line_in.product_code or (prod.internal_ref if prod else None) or (prod.barcode if prod else None)

        medida = line_in.uom_name
        if not medida and prod and prod.uom:
            medida = prod.uom.symbol or prod.uom.name or "PZA"

        qty_dec = Decimal(str(line_in.qty or 1.0))
        price_dec = Decimal(str(line_in.unit_price or 0.0))
        disc_pct_dec = Decimal(str(line_in.discount_pct or 0.0))

        gross = qty_dec * price_dec
        disc = gross * (disc_pct_dec / Decimal("100"))
        net = gross - disc
        sub = net / Decimal("1.12")
        tax = net - sub

        desc_line = f"MEDIDA:{medida}" if medida else ""

        line = SaleOrderLine(
            sale_id=quote.id,
            product_id=p_uuid,
            product_name=p_name,
            product_barcode=barcode,
            description=desc_line,
            qty=qty_dec,
            unit_price=price_dec,
            cost_price=c_price,
            discount_pct=disc_pct_dec,
            discount_amount=disc,
            subtotal=sub,
            tax_amount=tax,
            total=net
        )
        db.add(line)

        total_subtotal += sub
        total_tax += tax
        total_disc += disc
        total_final += net

    quote.subtotal = total_subtotal
    quote.tax_amount = total_tax
    quote.discount_amount = total_disc
    quote.total = total_final

    await db.commit()
    await db.refresh(quote)

    return {
        "id": str(quote.id),
        "number": quote.number,
        "total": float(quote.total),
        "state": "draft"
    }


# ===== 5. UPDATE QUOTE =====
@router.put("/{quote_id}")
async def update_quote(
    quote_id: str,
    data: QuoteUpdateIn,
    db: AsyncSession = Depends(get_db),
    current_user=Depends(get_current_user),
):
    """Actualizar una cotización existente."""
    try:
        q_uuid = uuid.UUID(quote_id)
    except ValueError:
        raise HTTPException(status_code=400, detail="ID de cotización inválido")

    stmt = (
        select(SaleOrder)
        .options(selectinload(SaleOrder.lines))
        .where(
            SaleOrder.id == q_uuid,
            SaleOrder.company_id == current_user.company_id,
            SaleOrder.sale_type == SaleType.QUOTE
        )
    )
    res = await db.execute(stmt)
    quote = res.scalar_one_or_none()
    if not quote:
        raise HTTPException(status_code=404, detail="Cotización no encontrada")

    if quote.state == SaleState.CONFIRMED:
        raise HTTPException(status_code=400, detail="No se puede modificar una cotización ya convertida a venta")

    if data.customer_name is not None:
        quote.customer_name = data.customer_name
    if data.customer_nit is not None:
        quote.customer_nit = data.customer_nit
    if data.customer_address is not None:
        quote.customer_address = data.customer_address
    if data.notes is not None:
        quote.notes = data.notes

    if data.customer_id is not None:
        try:
            quote.customer_id = uuid.UUID(data.customer_id) if data.customer_id else None
        except ValueError:
            pass

    if data.warehouse_id is not None:
        try:
            quote.warehouse_id = uuid.UUID(data.warehouse_id)
        except ValueError:
            pass

    if data.valid_days is not None:
        now = quote.sale_date or datetime.now(timezone.utc)
        quote.due_date = now + timedelta(days=data.valid_days)

    # Actualizar metadata extra
    extra = dict(quote.extra or {})
    if data.condition is not None:
        extra["condition"] = data.condition
    if data.price_tier is not None:
        extra["price_tier"] = data.price_tier
    if data.seller_name is not None:
        extra["seller_name"] = data.seller_name
    if data.customer_phone is not None:
        extra["customer_phone"] = data.customer_phone
    if data.customer_company is not None:
        extra["customer_company"] = data.customer_company
    if data.customer_code is not None:
        extra["customer_code"] = data.customer_code
    quote.extra = extra

    if data.lines is not None:
        quote.lines.clear()
        total_subtotal = Decimal("0")
        total_tax = Decimal("0")
        total_disc = Decimal("0")
        total_final = Decimal("0")

        for line_in in data.lines:
            try:
                p_uuid = uuid.UUID(line_in.product_id)
            except ValueError:
                continue

            p_res = await db.execute(
                select(Product).options(selectinload(Product.uom)).where(Product.id == p_uuid)
            )
            prod = p_res.scalar_one_or_none()
            p_name = line_in.product_name or (prod.name if prod else "Artículo")
            c_price = prod.cost_price if prod else Decimal("0")
            barcode = line_in.product_code or (prod.internal_ref if prod else None) or (prod.barcode if prod else None)

            medida = line_in.uom_name
            if not medida and prod and prod.uom:
                medida = prod.uom.symbol or prod.uom.name or "PZA"

            qty_dec = Decimal(str(line_in.qty or 1.0))
            price_dec = Decimal(str(line_in.unit_price or 0.0))
            disc_pct_dec = Decimal(str(line_in.discount_pct or 0.0))

            gross = qty_dec * price_dec
            disc = gross * (disc_pct_dec / Decimal("100"))
            net = gross - disc
            sub = net / Decimal("1.12")
            tax = net - sub

            desc_line = f"MEDIDA:{medida}" if medida else ""

            line = SaleOrderLine(
                sale_id=quote.id,
                product_id=p_uuid,
                product_name=p_name,
                product_barcode=barcode,
                description=desc_line,
                qty=qty_dec,
                unit_price=price_dec,
                cost_price=c_price,
                discount_pct=disc_pct_dec,
                discount_amount=disc,
                subtotal=sub,
                tax_amount=tax,
                total=net
            )
            quote.lines.append(line)

            total_subtotal += sub
            total_tax += tax
            total_disc += disc
            total_final += net

        quote.subtotal = total_subtotal
        quote.tax_amount = total_tax
        quote.discount_amount = total_disc
        quote.total = total_final

    await db.commit()
    await db.refresh(quote)

    now = datetime.now(timezone.utc)
    return {
        "id": str(quote.id),
        "number": quote.number,
        "total": float(quote.total),
        "state": _resolve_quote_state(quote, now)
    }


# ===== 6. UPDATE QUOTE STATE =====
@router.patch("/{quote_id}/state")
async def update_quote_state(
    quote_id: str,
    data: QuoteStateIn,
    db: AsyncSession = Depends(get_db),
    current_user=Depends(get_current_user),
):
    """Cambiar estado de cotización (draft, sent, cancelled)."""
    try:
        q_uuid = uuid.UUID(quote_id)
    except ValueError:
        raise HTTPException(status_code=400, detail="ID de cotización inválido")

    stmt = select(SaleOrder).where(
        SaleOrder.id == q_uuid,
        SaleOrder.company_id == current_user.company_id,
        SaleOrder.sale_type == SaleType.QUOTE
    )
    res = await db.execute(stmt)
    quote = res.scalar_one_or_none()
    if not quote:
        raise HTTPException(status_code=404, detail="Cotización no encontrada")

    if quote.state == SaleState.CONFIRMED:
        raise HTTPException(status_code=400, detail="No se puede cambiar estado de una cotización ya confirmada")

    extra = dict(quote.extra or {})
    target = data.state.lower().strip()

    if target == "sent":
        quote.state = SaleState.PENDING_APPROVAL
        extra["state_custom"] = "sent"
    elif target == "cancelled":
        quote.state = SaleState.CANCELLED
        extra["state_custom"] = "cancelled"
    elif target == "draft":
        quote.state = SaleState.DRAFT
        extra["state_custom"] = "draft"
    else:
        raise HTTPException(status_code=400, detail="Estado no permitido")

    quote.extra = extra
    await db.commit()

    now = datetime.now(timezone.utc)
    return {
        "id": str(quote.id),
        "state": _resolve_quote_state(quote, now),
        "message": f"Estado actualizado a '{target}'"
    }


# ===== 7. DUPLICATE QUOTE =====
@router.post("/{quote_id}/duplicate")
async def duplicate_quote(
    quote_id: str,
    db: AsyncSession = Depends(get_db),
    current_user=Depends(get_current_user),
):
    """Duplicar una cotización previa para crear una nueva versión borrador."""
    try:
        q_uuid = uuid.UUID(quote_id)
    except ValueError:
        raise HTTPException(status_code=400, detail="ID de cotización inválido")

    stmt = (
        select(SaleOrder)
        .options(selectinload(SaleOrder.lines))
        .where(
            SaleOrder.id == q_uuid,
            SaleOrder.company_id == current_user.company_id,
            SaleOrder.sale_type == SaleType.QUOTE
        )
    )
    res = await db.execute(stmt)
    orig = res.scalar_one_or_none()
    if not orig:
        raise HTTPException(status_code=404, detail="Cotización original no encontrada")

    # Generar Secuencia única COT-XXXXX
    all_numbers_res = await db.execute(
        select(SaleOrder.number).where(SaleOrder.number.like("COT-%"))
    )
    existing_numbers = set(r[0] for r in all_numbers_res.all())

    max_seq = 0
    for num in existing_numbers:
        try:
            val = int(num.replace("COT-", ""))
            if val > max_seq:
                max_seq = val
        except (ValueError, TypeError):
            pass

    seq = max(max_seq + 1, 1)
    new_number = f"COT-{seq:05d}"
    while new_number in existing_numbers:
        seq += 1
        new_number = f"COT-{seq:05d}"

    now = datetime.now(timezone.utc)
    due_date = now + timedelta(days=15)

    extra = dict(orig.extra or {})
    extra["state_custom"] = "draft"

    new_quote = SaleOrder(
        company_id=orig.company_id,
        branch_id=orig.branch_id,
        warehouse_id=orig.warehouse_id,
        customer_id=orig.customer_id,
        user_id=current_user.id,
        number=new_number,
        state=SaleState.DRAFT,
        sale_type=SaleType.QUOTE,
        sale_date=now,
        due_date=due_date,
        customer_name=orig.customer_name,
        customer_nit=orig.customer_nit,
        customer_address=orig.customer_address,
        notes=orig.notes,
        subtotal=orig.subtotal,
        discount_amount=orig.discount_amount,
        tax_amount=orig.tax_amount,
        total=orig.total,
        extra=extra
    )
    db.add(new_quote)
    await db.flush()

    for line in orig.lines:
        new_line = SaleOrderLine(
            sale_id=new_quote.id,
            product_id=line.product_id,
            product_name=line.product_name,
            product_barcode=line.product_barcode,
            description=line.description,
            qty=line.qty,
            unit_price=line.unit_price,
            cost_price=line.cost_price,
            discount_pct=line.discount_pct,
            discount_amount=line.discount_amount,
            subtotal=line.subtotal,
            tax_amount=line.tax_amount,
            total=line.total
        )
        db.add(new_line)

    await db.commit()
    await db.refresh(new_quote)

    return {
        "id": str(new_quote.id),
        "number": new_quote.number,
        "message": f"Cotización duplicada con éxito bajo el No. {new_quote.number}"
    }


# ===== 8. SEND EMAIL =====
@router.post("/{quote_id}/send-email")
async def send_quote_email(
    quote_id: str,
    data: EmailQuoteIn,
    db: AsyncSession = Depends(get_db),
    current_user=Depends(get_current_user),
):
    """Enviar proforma por correo electrónico con desglose oficial."""
    try:
        q_uuid = uuid.UUID(quote_id)
    except ValueError:
        raise HTTPException(status_code=400, detail="ID de cotización inválido")

    stmt = (
        select(SaleOrder)
        .options(
            selectinload(SaleOrder.lines),
            selectinload(SaleOrder.customer),
            selectinload(SaleOrder.user)
        )
        .where(
            SaleOrder.id == q_uuid,
            SaleOrder.company_id == current_user.company_id,
            SaleOrder.sale_type == SaleType.QUOTE
        )
    )
    res = await db.execute(stmt)
    quote = res.scalar_one_or_none()
    if not quote:
        raise HTTPException(status_code=404, detail="Cotización no encontrada")

    extra = quote.extra or {}
    recipient = data.recipient_email.strip()

    # Obtener membrete oficial de la empresa de la cotización
    comp_res = await db.execute(select(Company).where(Company.id == quote.company_id))
    company = comp_res.scalar_one_or_none()
    comp_branding = get_company_branding(company)
    company_title = comp_branding.get("title_name") or comp_branding.get("name") or "Cotización Comercial"
    company_address = comp_branding.get("address") or ""
    company_nit = comp_branding.get("nit") or "C/F"
    company_phone = comp_branding.get("phone") or ""
    company_email = comp_branding.get("email") or comp_branding.get("emails") or ""

    subject = data.subject or f"Cotización {quote.number} — {company_title}"
    
    # Preparar resumen de líneas para texto y HTML
    lines_rows = ""
    lines_text = ""
    for idx, l in enumerate(quote.lines, 1):
        lines_rows += f"""
        <tr>
            <td style="padding:8px 10px; border-bottom:1px solid #e2e8f0;">{l.product_name}</td>
            <td style="padding:8px 10px; text-align:center; border-bottom:1px solid #e2e8f0;">{float(l.qty)}</td>
            <td style="padding:8px 10px; text-align:right; border-bottom:1px solid #e2e8f0;">Q {float(l.unit_price):.2f}</td>
            <td style="padding:8px 10px; text-align:right; border-bottom:1px solid #e2e8f0; font-weight:bold;">Q {float(l.total):.2f}</td>
        </tr>
        """
        lines_text += f"{idx}. {l.product_name} | Cant: {float(l.qty)} | P.U: Q {float(l.unit_price):.2f} | Total: Q {float(l.total):.2f}\n"

    # Preparar mailto fallback URL
    body_text = f"""Estimado(a) {quote.customer_name},

Adjuntamos el resumen de su Cotización No. {quote.number} emitida por {company_title.upper()}.

Condición: {extra.get('condition', 'Contado')}
Válida hasta: {quote.due_date.strftime('%d/%m/%Y') if quote.due_date else '15 días'}

Detalle de productos:
{lines_text}
Subtotal: Q {float(quote.subtotal or 0):.2f}
IVA (12%): Q {float(quote.tax_amount or 0):.2f}
TOTAL COTIZADO: Q {float(quote.total or 0):.2f}

{quote.notes or ''}

Atentamente,
{company_title.upper()}
Tel: {company_phone} | {company_email}
{company_address}
"""
    mailto_url = f"mailto:{recipient}?subject={urllib.parse.quote(subject)}&body={urllib.parse.quote(body_text)}"

    # Verificar si hay servidor SMTP configurado
    smtp_host = os.environ.get("SMTP_HOST")
    smtp_port = int(os.environ.get("SMTP_PORT", 587))
    smtp_user = os.environ.get("SMTP_USER")
    smtp_pass = os.environ.get("SMTP_PASSWORD")

    if smtp_host and smtp_user and smtp_pass:
        try:
            msg = MIMEMultipart("alternative")
            msg["Subject"] = subject
            msg["From"] = f"{company_title} <{smtp_user}>"
            msg["To"] = recipient

            header_contact = " | ".join(filter(bool, [
                company_address,
                f"NIT: {company_nit}" if company_nit else "",
                f"Tel: {company_phone}" if company_phone else ""
            ]))

            html_content = f"""
            <html>
            <body style="font-family:Arial, sans-serif; color:#1e293b; background:#f8fafc; padding:20px;">
                <div style="max-width:650px; margin:0 auto; background:#ffffff; border-radius:8px; border:1px solid #e2e8f0; overflow:hidden;">
                    <div style="background:#562B82; color:#ffffff; padding:20px; text-align:center;">
                        <h2 style="margin:0; font-size:20px;">{company_title.upper()}</h2>
                        <p style="margin:4px 0 0 0; font-size:12px;">{header_contact}</p>
                    </div>
                    <div style="padding:24px;">
                        <div style="display:flex; justify-content:space-between; margin-bottom:18px;">
                            <div>
                                <strong>Cotización:</strong> {quote.number}<br>
                                <strong>Cliente:</strong> {quote.customer_name}<br>
                                <strong>NIT:</strong> {quote.customer_nit or 'CF'}
                            </div>
                            <div style="text-align:right;">
                                <strong>Fecha:</strong> {quote.sale_date.strftime('%d/%m/%Y') if quote.sale_date else ''}<br>
                                <strong>Vence:</strong> {quote.due_date.strftime('%d/%m/%Y') if quote.due_date else '15 días'}<br>
                                <strong>Condición:</strong> {extra.get('condition', 'Contado')}
                            </div>
                        </div>

                        {f'<p style="background:#f1f5f9; padding:10px; border-radius:6px; font-size:13px;">{data.message}</p>' if data.message else ''}

                        <table style="width:100%; border-collapse:collapse; margin-top:16px; font-size:13px;">
                            <thead>
                                <tr style="background:#f8fafc; text-align:left; color:#475569;">
                                    <th style="padding:8px 10px; border-bottom:2px solid #cbd5e1;">Descripción</th>
                                    <th style="padding:8px 10px; text-align:center; border-bottom:2px solid #cbd5e1;">Cant.</th>
                                    <th style="padding:8px 10px; text-align:right; border-bottom:2px solid #cbd5e1;">P. Unit.</th>
                                    <th style="padding:8px 10px; text-align:right; border-bottom:2px solid #cbd5e1;">Total</th>
                                </tr>
                            </thead>
                            <tbody>
                                {lines_rows}
                            </tbody>
                        </table>

                        <div style="margin-top:20px; text-align:right; font-size:14px;">
                            <div>Subtotal: <strong>Q {float(quote.subtotal or 0):.2f}</strong></div>
                            <div>IVA (12%): <strong>Q {float(quote.tax_amount or 0):.2f}</strong></div>
                            <div style="font-size:18px; color:#562B82; font-weight:bold; margin-top:8px;">
                                TOTAL: Q {float(quote.total or 0):.2f}
                            </div>
                        </div>

                        <hr style="margin:24px 0; border:none; border-top:1px solid #e2e8f0;">
                        <p style="font-size:11px; color:#64748b; line-height:1.4;">
                            * La presente cotización es informativa y no constituye venta contable ni reserva definitiva de producto hasta su confirmación.
                        </p>
                    </div>
                </div>
            </body>
            </html>
            """
            msg.attach(MIMEText(body_text, "plain"))
            msg.attach(MIMEText(html_content, "html"))

            with smtplib.SMTP(smtp_host, smtp_port, timeout=10) as server:
                server.starttls()
                server.login(smtp_user, smtp_pass)
                server.sendmail(smtp_user, [recipient], msg.as_string())

            # Marcar automáticamente como enviada
            extra["state_custom"] = "sent"
            quote.state = SaleState.PENDING_APPROVAL
            quote.extra = extra
            await db.commit()

            return {
                "status": "sent",
                "message": f"Cotización enviada exitosamente a {recipient}"
            }
        except Exception as e:
            # Si falla SMTP, devolvemos fallback mailto
            return {
                "status": "fallback",
                "message": f"Error SMTP ({str(e)}). Abriendo cliente de correo...",
                "mailto_url": mailto_url
            }

    # Sin SMTP configurado: fallback mailto
    # Marcar como enviada
    extra["state_custom"] = "sent"
    quote.state = SaleState.PENDING_APPROVAL
    quote.extra = extra
    await db.commit()

    return {
        "status": "fallback",
        "message": "Preparando envío a través de tu aplicación de correo...",
        "mailto_url": mailto_url
    }


# ===== 9. CONVERT QUOTE TO SALE ORDER =====
@router.post("/{quote_id}/convert")
async def convert_quote_to_sale(
    quote_id: str,
    db: AsyncSession = Depends(get_db),
    current_user=Depends(get_current_user),
):
    """Convertir una cotización aprobada en orden de venta confirmada y descontar stock."""
    try:
        q_uuid = uuid.UUID(quote_id)
    except ValueError:
        raise HTTPException(status_code=400, detail="ID de cotización inválido")

    stmt = (
        select(SaleOrder)
        .options(selectinload(SaleOrder.lines).selectinload(SaleOrderLine.product))
        .where(
            SaleOrder.id == q_uuid,
            SaleOrder.company_id == current_user.company_id,
            SaleOrder.sale_type == SaleType.QUOTE
        )
    )
    res = await db.execute(stmt)
    quote = res.scalar_one_or_none()
    if not quote:
        raise HTTPException(status_code=404, detail="Cotización no encontrada")

    if quote.state == SaleState.CONFIRMED:
        return {"status": "already_converted", "sale_id": str(quote.id), "number": quote.number}

    if not quote.lines:
        raise HTTPException(status_code=400, detail="La cotización no contiene productos para procesar la venta.")

    # Asegurar bodega de despacho
    wh_id = quote.warehouse_id
    if not wh_id:
        w_res = await db.execute(
            select(Warehouse.id)
            .where(Warehouse.company_id == current_user.company_id, Warehouse.is_active == True)
            .limit(1)
        )
        wh_id = w_res.scalar_one_or_none()
        if not wh_id:
            w_res = await db.execute(select(Warehouse.id).limit(1))
            wh_id = w_res.scalar_one_or_none()
        quote.warehouse_id = wh_id

    # 1. Agrupar cantidades requeridas por producto para validación integral
    qty_by_product = {}
    lines_by_product = {}
    for line in quote.lines:
        if line.product_id:
            req_qty = Decimal(str(line.qty or 1))
            qty_by_product[line.product_id] = qty_by_product.get(line.product_id, Decimal("0")) + req_qty
            if line.product_id not in lines_by_product:
                lines_by_product[line.product_id] = []
            lines_by_product[line.product_id].append(line)

    # 2. VALIDACIÓN ESTRICTA DE STOCK: No permitir vender si el stock está en 0 o es menor a lo solicitado
    out_of_stock_errors = []
    quants_map = {}

    for p_id, total_req in qty_by_product.items():
        q_stmt = select(StockQuant).where(
            StockQuant.product_id == p_id,
            StockQuant.warehouse_id == wh_id,
            StockQuant.is_deleted == False
        ).limit(1)
        q_res = await db.execute(q_stmt)
        quant = q_res.scalar_one_or_none()

        avail_stock = Decimal(str(quant.qty_on_hand)) if (quant and quant.qty_on_hand is not None) else Decimal("0")
        
        sample_line = lines_by_product[p_id][0]
        prod_obj = sample_line.product
        p_name = prod_obj.name if (prod_obj and prod_obj.name) else (sample_line.description or "Producto")
        p_code = getattr(prod_obj, "internal_ref", None) or getattr(prod_obj, "barcode", None) or ""
        ref = f"{p_name} [{p_code}]" if p_code else p_name

        if avail_stock <= Decimal("0"):
            out_of_stock_errors.append(f"• {ref}: Stock agotado en bodega (0 disponible)")
        elif avail_stock < total_req:
            out_of_stock_errors.append(f"• {ref}: Stock insuficiente (Disponible: {avail_stock}, Solicitado: {total_req})")
        else:
            quants_map[p_id] = quant

    if out_of_stock_errors:
        err_msg = (
            "No se puede realizar la venta porque hay productos sin existencias suficientes en la bodega:\n\n"
            + "\n".join(out_of_stock_errors)
            + "\n\nPor favor ajusta las cantidades o ingresa inventario antes de procesar la venta."
        )
        raise HTTPException(status_code=400, detail=err_msg)

    # 3. Descontar inventario y asentar los movimientos de salida
    for line in quote.lines:
        if line.product_id and line.product_id in quants_map:
            quant = quants_map[line.product_id]
            if quant:
                quant.qty_on_hand = (quant.qty_on_hand or Decimal("0")) - Decimal(str(line.qty))

            move = StockMove(
                warehouse_id=wh_id,
                product_id=line.product_id,
                move_type=StockMoveType.SALE,
                qty=Decimal(str(line.qty)),
                unit_cost=line.cost_price or Decimal("0"),
                total_cost=(line.cost_price or Decimal("0")) * Decimal(str(line.qty)),
                reference=f"Venta desde Cotización {quote.number}",
                notes=f"Cliente: {quote.customer_name}"
            )
            db.add(move)

    # Actualizar estado de cotización a CONFIRMED
    quote.state = SaleState.CONFIRMED
    extra = dict(quote.extra or {})
    extra["state_custom"] = "converted"
    quote.extra = extra

    await db.commit()

    return {
        "status": "converted",
        "state": "converted",
        "sale_id": str(quote.id),
        "number": quote.number,
        "message": f"¡Cotización {quote.number} aprobada y convertida a venta exitosamente!"
    }


# ===== 10. DELETE / CANCEL QUOTE =====
@router.delete("/{quote_id}")
async def delete_quote(
    quote_id: str,
    db: AsyncSession = Depends(get_db),
    current_user=Depends(get_current_user),
):
    """Cancelar o marcar como eliminada una cotización."""
    try:
        q_uuid = uuid.UUID(quote_id)
    except ValueError:
        raise HTTPException(status_code=400, detail="ID de cotización inválido")

    stmt = select(SaleOrder).where(
        SaleOrder.id == q_uuid,
        SaleOrder.company_id == current_user.company_id,
        SaleOrder.sale_type == SaleType.QUOTE
    )
    res = await db.execute(stmt)
    quote = res.scalar_one_or_none()
    if not quote:
        raise HTTPException(status_code=404, detail="Cotización no encontrada")

    if quote.state == SaleState.CONFIRMED:
        raise HTTPException(status_code=400, detail="No se puede eliminar una cotización que ya fue convertida a venta")

    quote.is_deleted = True
    quote.state = SaleState.CANCELLED
    extra = dict(quote.extra or {})
    extra["state_custom"] = "cancelled"
    quote.extra = extra

    await db.commit()

    return {"status": "success", "message": f"Cotización {quote.number} cancelada exitosamente"}
