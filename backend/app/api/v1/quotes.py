"""API v1 — Cotizaciones / Proformas Comerciales 360°."""
from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy import select, and_, func, desc
from sqlalchemy.orm import selectinload
from pydantic import BaseModel, ConfigDict
from typing import Optional, List
from decimal import Decimal
from datetime import datetime, timezone, timedelta
import uuid

from app.core.database import get_db
from app.core.security import get_current_user
from app.models.sale import SaleOrder, SaleOrderLine, SaleState, SaleType
from app.models.partner import Partner, PartnerType
from app.models.product import Product
from app.models.company import Branch, Warehouse
from app.models.inventory import StockQuant, StockMove, StockMoveType

router = APIRouter(prefix="/quotes")


# ===== SCHEMAS =====
class QuoteLineIn(BaseModel):
    product_id: str
    product_name: Optional[str] = None
    qty: float = 1.0
    unit_price: float
    discount_pct: float = 0.0


class QuoteCreateIn(BaseModel):
    customer_id: Optional[str] = None
    customer_name: Optional[str] = "Consumidor Final"
    customer_nit: Optional[str] = "CF"
    customer_address: Optional[str] = ""
    valid_days: int = 15
    notes: Optional[str] = None
    branch_id: Optional[str] = None
    warehouse_id: Optional[str] = None
    lines: List[QuoteLineIn] = []


class QuoteUpdateIn(BaseModel):
    customer_id: Optional[str] = None
    customer_name: Optional[str] = None
    customer_nit: Optional[str] = None
    customer_address: Optional[str] = None
    valid_days: Optional[int] = 15
    notes: Optional[str] = None
    lines: Optional[List[QuoteLineIn]] = None


# ===== 1. KPIS =====
@router.get("/kpis")
async def get_quotes_kpis(
    db: AsyncSession = Depends(get_db),
    current_user=Depends(get_current_user),
):
    """KPIs del módulo de cotizaciones."""
    c_uuid = uuid.UUID(str(current_user.company_id))

    stmt = select(
        func.count(SaleOrder.id).label("total"),
        func.sum(SaleOrder.total).label("total_amount"),
        func.sum(func.case((SaleOrder.state == SaleState.DRAFT, 1), else_=0)).label("draft_count"),
        func.sum(func.case((SaleOrder.state == SaleState.CONFIRMED, 1), else_=0)).label("converted_count"),
    ).where(
        SaleOrder.company_id == c_uuid,
        SaleOrder.sale_type == SaleType.QUOTE,
        SaleOrder.is_deleted == False
    )
    res = await db.execute(stmt)
    row = res.one()

    return {
        "total_quotes": int(row.total or 0),
        "total_amount": float(row.total_amount or 0.0),
        "draft_count": int(row.draft_count or 0),
        "converted_count": int(row.converted_count or 0),
    }


# ===== 2. LIST QUOTES =====
@router.get("/")
async def list_quotes(
    state: Optional[str] = Query(None),
    search: Optional[str] = Query(None),
    db: AsyncSession = Depends(get_db),
    current_user=Depends(get_current_user),
):
    """Listar cotizaciones con filtros."""
    c_uuid = uuid.UUID(str(current_user.company_id))

    stmt = (
        select(SaleOrder)
        .options(selectinload(SaleOrder.customer))
        .where(
            SaleOrder.company_id == c_uuid,
            SaleOrder.sale_type == SaleType.QUOTE,
            SaleOrder.is_deleted == False
        )
        .order_by(SaleOrder.created_at.desc())
    )

    if state:
        if state == "draft":
            stmt = stmt.where(SaleOrder.state == SaleState.DRAFT)
        elif state == "confirmed":
            stmt = stmt.where(SaleOrder.state == SaleState.CONFIRMED)
        elif state == "cancelled":
            stmt = stmt.where(SaleOrder.state == SaleState.CANCELLED)

    res = await db.execute(stmt)
    quotes = res.scalars().all()

    items = []
    for q in quotes:
        # Check search
        if search:
            s_low = search.lower()
            q_num = (q.number or "").lower()
            c_name = (q.customer_name or "").lower()
            c_nit = (q.customer_nit or "").lower()
            if s_low not in q_num and s_low not in c_name and s_low not in c_nit:
                continue

        created_str = q.sale_date.strftime("%d/%m/%Y") if q.sale_date else ""
        due_str = q.due_date.strftime("%d/%m/%Y") if q.due_date else ""

        # Status text
        st = "draft"
        if q.state == SaleState.CONFIRMED:
            st = "converted"
        elif q.state == SaleState.CANCELLED:
            st = "cancelled"

        items.append({
            "id": str(q.id),
            "number": q.number,
            "date": created_str,
            "valid_until": due_str,
            "customer_name": q.customer_name or "Consumidor Final",
            "customer_nit": q.customer_nit or "CF",
            "subtotal": float(q.subtotal or 0),
            "tax_amount": float(q.tax_amount or 0),
            "total": float(q.total or 0),
            "state": st,
            "notes": q.customer_address or ""
        })

    return items


# ===== 3. GET SINGLE QUOTE =====
@router.get("/{quote_id}")
async def get_quote(
    quote_id: str,
    db: AsyncSession = Depends(get_db),
    current_user=Depends(get_current_user),
):
    """Obtener detalle completo de una cotización."""
    try:
        q_uuid = uuid.UUID(quote_id)
    except ValueError:
        raise HTTPException(status_code=400, detail="ID de cotización inválido")

    stmt = (
        select(SaleOrder)
        .options(
            selectinload(SaleOrder.lines),
            selectinload(SaleOrder.customer),
            selectinload(SaleOrder.branch),
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

    st = "draft"
    if quote.state == SaleState.CONFIRMED:
        st = "converted"
    elif quote.state == SaleState.CANCELLED:
        st = "cancelled"

    return {
        "id": str(quote.id),
        "number": quote.number,
        "date": quote.sale_date.strftime("%d/%m/%Y") if quote.sale_date else "",
        "valid_until": quote.due_date.strftime("%d/%m/%Y") if quote.due_date else "",
        "customer_id": str(quote.customer_id) if quote.customer_id else None,
        "customer_name": quote.customer_name or "Consumidor Final",
        "customer_nit": quote.customer_nit or "CF",
        "customer_address": quote.customer_address or "",
        "branch_id": str(quote.branch_id),
        "branch_name": quote.branch.name if quote.branch else "Sucursal Principal",
        "warehouse_id": str(quote.warehouse_id),
        "subtotal": float(quote.subtotal or 0),
        "discount_amount": float(quote.discount_amount or 0),
        "tax_amount": float(quote.tax_amount or 0),
        "total": float(quote.total or 0),
        "state": st,
        "lines": [
            {
                "id": str(l.id),
                "product_id": str(l.product_id),
                "product_name": l.product_name,
                "qty": float(l.qty),
                "unit_price": float(l.unit_price),
                "discount_pct": float(l.discount_pct or 0),
                "subtotal": float(l.subtotal or 0),
                "tax_amount": float(l.tax_amount or 0),
                "total": float(l.total or 0)
            }
            for l in quote.lines
        ]
    }


# ===== 4. CREATE QUOTE =====
@router.post("/")
async def create_quote(
    data: QuoteCreateIn,
    db: AsyncSession = Depends(get_db),
    current_user=Depends(get_current_user),
):
    """Crear una nueva cotización/proforma."""
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

    # Generar Secuencia única COT-XXXXX a nivel global para garantizar la restricción UNIQUE
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

    # Customer
    cust_uuid = None
    if data.customer_id:
        try:
            cust_uuid = uuid.UUID(data.customer_id)
        except ValueError:
            pass

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

        p_res = await db.execute(select(Product).where(Product.id == p_uuid))
        prod = p_res.scalar_one_or_none()
        p_name = line_in.product_name or (prod.name if prod else "Artículo")
        c_price = prod.cost_price if prod else Decimal("0")

        qty_dec = Decimal(str(line_in.qty or 1.0))
        price_dec = Decimal(str(line_in.unit_price or 0.0))
        disc_pct_dec = Decimal(str(line_in.discount_pct or 0.0))

        gross = qty_dec * price_dec
        disc = gross * (disc_pct_dec / Decimal("100"))
        net = gross - disc
        sub = net / Decimal("1.12")
        tax = net - sub

        line = SaleOrderLine(
            sale_id=quote.id,
            product_id=p_uuid,
            product_name=p_name,
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
    if data.customer_id is not None:
        try:
            quote.customer_id = uuid.UUID(data.customer_id) if data.customer_id else None
        except ValueError:
            pass
    if data.valid_days is not None:
        now = quote.sale_date or datetime.now(timezone.utc)
        quote.due_date = now + timedelta(days=data.valid_days)

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

            p_res = await db.execute(select(Product).where(Product.id == p_uuid))
            prod = p_res.scalar_one_or_none()
            p_name = line_in.product_name or (prod.name if prod else "Artículo")
            c_price = prod.cost_price if prod else Decimal("0")

            qty_dec = Decimal(str(line_in.qty or 1.0))
            price_dec = Decimal(str(line_in.unit_price or 0.0))
            disc_pct_dec = Decimal(str(line_in.discount_pct or 0.0))

            gross = qty_dec * price_dec
            disc = gross * (disc_pct_dec / Decimal("100"))
            net = gross - disc
            sub = net / Decimal("1.12")
            tax = net - sub

            line = SaleOrderLine(
                sale_id=quote.id,
                product_id=p_uuid,
                product_name=p_name,
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

    return {
        "id": str(quote.id),
        "number": quote.number,
        "total": float(quote.total),
        "state": "draft"
    }


# ===== 6. CONVERT QUOTE TO SALE ORDER =====
@router.post("/{quote_id}/convert")
async def convert_quote_to_sale(
    quote_id: str,
    db: AsyncSession = Depends(get_db),
    current_user=Depends(get_current_user),
):
    """Convertir una cotización aprobada en orden de venta confirmada."""
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
        return {"status": "already_converted", "sale_id": str(quote.id), "number": quote.number}

    # Descontar inventario por cada línea
    for line in quote.lines:
        if line.product_id:
            # Buscar quant
            q_stmt = select(StockQuant).where(
                StockQuant.product_id == line.product_id,
                StockQuant.warehouse_id == quote.warehouse_id
            ).limit(1)
            q_res = await db.execute(q_stmt)
            quant = q_res.scalar_one_or_none()
            if quant:
                quant.qty_on_hand = (quant.qty_on_hand or Decimal("0")) - Decimal(str(line.qty))

            # Registrar movimiento de salida
            move = StockMove(
                warehouse_id=quote.warehouse_id,
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
    await db.commit()

    return {
        "status": "converted",
        "sale_id": str(quote.id),
        "number": quote.number,
        "message": f"¡Cotización {quote.number} convertida a venta exitosamente!"
    }
