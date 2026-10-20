"""API v1 — Módulo de Pre-venta y Solicitudes POS.
Flujo comercial donde preventistas generan pedidos en mostrador/campo
que requieren autorización y validación con bodega antes de rebajar inventario.
"""
from fastapi import APIRouter, Depends, HTTPException, Query, status
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy import select, and_, func, desc
from sqlalchemy.orm import selectinload
from pydantic import BaseModel, ConfigDict
from typing import Optional, List
from decimal import Decimal
from datetime import datetime, timezone, timedelta
import uuid
import random

from app.core.database import get_db
from app.core.security import get_current_user
from app.models.sale import SaleOrder, SaleOrderLine, SaleState, SaleType
from app.models.product import Product, ProductType
from app.models.partner import Partner
from app.models.company import Branch, Warehouse
from app.models.inventory import StockQuant, StockMove, StockMoveType
from app.models.user import User
from app.models.ar import CustomerInvoiceCredit

import time

router = APIRouter(prefix="/presale")

# Caché en memoria para evitar ejecutar queries de expiración continuamente (throttle: 5 minutos)
_LAST_EXPIRE_CHECK: dict[str, float] = {}
EXPIRE_INTERVAL_SECONDS = 300


async def auto_expire_stale_presales(db: AsyncSession, company_id: uuid.UUID) -> int:
    """
    Expira automáticamente solicitudes de pre-venta pendientes tras 7 días sin autorización o anulación.
    Libera el stock congelado volviendo a la normalidad de forma transparente.
    No afecta movimientos físicos de inventario ni Kardex.
    Incluye throttle en memoria (máximo 1 revisión cada 5 minutos por empresa).
    """
    now = time.time()
    cid_str = str(company_id)
    if (now - _LAST_EXPIRE_CHECK.get(cid_str, 0.0)) < EXPIRE_INTERVAL_SECONDS:
        return 0
    _LAST_EXPIRE_CHECK[cid_str] = now

    cutoff = datetime.now(timezone.utc) - timedelta(days=7)
    stmt = (
        select(SaleOrder)
        .where(
            SaleOrder.company_id == company_id,
            SaleOrder.sale_type == SaleType.PRESALE,
            SaleOrder.state.in_([SaleState.PENDING_APPROVAL, SaleState.WAREHOUSE_VALIDATION]),
            SaleOrder.created_at < cutoff,
            SaleOrder.is_deleted == False
        )
    )
    res = await db.execute(stmt)
    stale_orders = res.scalars().all()
    if stale_orders:
        for order in stale_orders:
            order.state = SaleState.CANCELLED
            order.rejection_reason = "Vencida automáticamente tras 7 días sin autorización. Stock congelado liberado a la normalidad."
            # Cancelar cuentas por cobrar huérfanas si existieran
            inv_stmt = select(CustomerInvoiceCredit).where(
                CustomerInvoiceCredit.sale_id == order.id,
                CustomerInvoiceCredit.company_id == company_id,
                CustomerInvoiceCredit.is_deleted == False
            )
            invs = (await db.execute(inv_stmt)).scalars().all()
            for inv in invs:
                inv.is_deleted = True
                inv.state = "cancelled"
        await db.commit()
    return len(stale_orders)


def _iso_utc(dt: Optional[datetime]) -> Optional[str]:
    if not dt:
        return None
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    return dt.isoformat()


# ===== SCHEMAS =====
class PresaleLineIn(BaseModel):
    product_id: str
    product_name: Optional[str] = None
    qty: float = 1.0
    unit_price: float
    discount_amount: float = 0.0
    notes: Optional[str] = None


class PresaleCreateIn(BaseModel):
    customer_id: Optional[str] = None
    customer_name: Optional[str] = "CONSUMIDOR FINAL"
    customer_nit: Optional[str] = "CF"
    customer_address: Optional[str] = "CIUDAD"
    branch_id: Optional[str] = None
    warehouse_id: Optional[str] = None
    notes: Optional[str] = None
    delivery_date: Optional[str] = None
    delivery_address: Optional[str] = None
    delivery_notes: Optional[str] = None
    lines: List[PresaleLineIn] = []


class PresaleRejectIn(BaseModel):
    reason: str


class PresaleApproveIn(BaseModel):
    warehouse_id: Optional[str] = None
    internal_notes: Optional[str] = None


class PreparedItemIn(BaseModel):
    line_id: Optional[str] = None
    product_id: Optional[str] = None
    is_prepared: bool = True
    qty_prepared: Optional[float] = None


class PresaleMarkPreparedIn(BaseModel):
    items: Optional[List[PreparedItemIn]] = None
    notes: Optional[str] = None


# ===== 1. KPIS SOLICITUDES POS =====
@router.get("/kpis")
async def get_presale_kpis(
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    """Métricas rápidas para el tablero de Solicitudes POS."""
    company_id = current_user.company_id
    await auto_expire_stale_presales(db, company_id)
    today_start = datetime.now(timezone.utc).replace(hour=0, minute=0, second=0, microsecond=0)

    # 1. Pendientes
    pending_stmt = select(
        func.count(SaleOrder.id),
        func.coalesce(func.sum(SaleOrder.total), 0)
    ).where(
        and_(
            SaleOrder.company_id == company_id,
            SaleOrder.sale_type == SaleType.PRESALE,
            SaleOrder.state.in_([SaleState.PENDING_APPROVAL, SaleState.WAREHOUSE_VALIDATION])
        )
    )
    pending_res = (await db.execute(pending_stmt)).first()
    pending_count = pending_res[0] or 0
    pending_amount = float(pending_res[1] or 0)

    # 2. Aprobadas hoy
    approved_today_stmt = select(func.count(SaleOrder.id)).where(
        and_(
            SaleOrder.company_id == company_id,
            SaleOrder.sale_type == SaleType.PRESALE,
            SaleOrder.state.in_([SaleState.APPROVED, SaleState.CONFIRMED, SaleState.INVOICED, SaleState.PAID]),
            SaleOrder.approved_at >= today_start
        )
    )
    approved_today = (await db.execute(approved_today_stmt)).scalar() or 0

    # 3. Rechazadas hoy
    rejected_today_stmt = select(func.count(SaleOrder.id)).where(
        and_(
            SaleOrder.company_id == company_id,
            SaleOrder.sale_type == SaleType.PRESALE,
            SaleOrder.state == SaleState.REJECTED,
            SaleOrder.approved_at >= today_start
        )
    )
    rejected_today = (await db.execute(rejected_today_stmt)).scalar() or 0

    return {
        "pending_count": pending_count,
        "pending_amount": round(pending_amount, 2),
        "approved_today": approved_today,
        "rejected_today": rejected_today,
    }


# ===== 2. CREAR PRE-VENTA (SOLICITUD POS) =====
@router.post("/", status_code=status.HTTP_201_CREATED)
async def create_presale(
    payload: PresaleCreateIn,
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    """
    Crea una Solicitud de Pre-venta.
    IMPORTANTE: No descuenta inventario ni genera movimientos de Kardex.
    Queda en estado 'pending_approval' a la espera de autorización.
    El stock del producto queda congelado hasta por 7 días o hasta aprobación/anulación.
    """
    company_id = current_user.company_id
    await auto_expire_stale_presales(db, company_id)
    if not payload.lines:
        raise HTTPException(status_code=400, detail="La solicitud de pre-venta debe incluir al menos un producto.")

    # Resolver sucursal y bodega predeterminadas
    branch_id = payload.branch_id
    if not branch_id:
        branch_id = current_user.current_branch_id
        if not branch_id:
            branch_match = (await db.execute(select(Branch).where(Branch.company_id == company_id).limit(1))).scalars().first()
            if not branch_match:
                raise HTTPException(status_code=400, detail="No hay sucursales configuradas en el sistema.")
            branch_id = branch_match.id

    warehouse_id = payload.warehouse_id
    if not warehouse_id:
        wh_match = (await db.execute(
            select(Warehouse).where(Warehouse.branch_id == branch_id).limit(1)
        )).scalars().first()
        if not wh_match:
            wh_match = (await db.execute(select(Warehouse).limit(1))).scalars().first()
        if not wh_match:
            raise HTTPException(status_code=400, detail="No hay bodegas configuradas en el sistema.")
        warehouse_id = wh_match.id

    # Generar correlativo PRE-YYYYMMDD-XXXX
    date_prefix = datetime.now(timezone.utc).strftime("%Y%m%d")
    rnd_suffix = random.randint(100000, 999999)
    order_number = f"PRE-{date_prefix}-{rnd_suffix}"

    # Calcular montos
    subtotal_accum = Decimal("0")
    total_accum = Decimal("0")
    order_lines = []

    for idx, line_data in enumerate(payload.lines, start=1):
        p_id = uuid.UUID(line_data.product_id) if isinstance(line_data.product_id, str) else line_data.product_id
        prod = (await db.execute(select(Product).where(Product.id == p_id))).scalars().first()
        if not prod:
            raise HTTPException(status_code=404, detail=f"Producto no encontrado: {line_data.product_id}")

        qty = Decimal(str(line_data.qty))
        price = Decimal(str(line_data.unit_price))
        disc = Decimal(str(line_data.discount_amount or 0))

        # Validar que ningún producto sobrepase la existencia disponible en bodega ni las reservas activas
        if getattr(prod, "product_type", None) != ProductType.SERVICE:
            # 1. Stock reservado por pre-ventas pendientes
            froz_q = float((await db.execute(
                select(func.coalesce(func.sum(SaleOrderLine.qty), 0))
                .join(SaleOrder, SaleOrderLine.sale_id == SaleOrder.id)
                .where(
                    SaleOrder.company_id == company_id,
                    SaleOrder.sale_type == SaleType.PRESALE,
                    SaleOrder.state.in_([SaleState.PENDING_APPROVAL, SaleState.WAREHOUSE_VALIDATION]),
                    SaleOrder.created_at >= (datetime.now(timezone.utc) - timedelta(days=7)),
                    SaleOrder.is_deleted == False,
                    SaleOrderLine.is_deleted == False,
                    SaleOrderLine.product_id == p_id,
                )
            )).scalar() or 0.0)

            # 2. Stock físico real en las bodegas activas de la empresa
            phys_q = float((await db.execute(
                select(func.coalesce(func.sum(StockQuant.qty_on_hand), 0))
                .join(Warehouse, StockQuant.warehouse_id == Warehouse.id)
                .join(Branch, Warehouse.branch_id == Branch.id)
                .where(
                    StockQuant.product_id == p_id,
                    StockQuant.is_deleted == False,
                    Warehouse.is_active == True,
                    Warehouse.is_deleted == False,
                    Branch.company_id == company_id,
                )
            )).scalar() or 0.0)

            avail_q = phys_q - froz_q
            req_qty = float(qty)

            if avail_q < req_qty:
                if froz_q > 0:
                    raise HTTPException(
                        status_code=400,
                        detail=f"No se puede generar la pre-venta para '{prod.name}'. El producto está reservado y no cuenta con existencia disponible suficiente (Disponible: {max(0, avail_q):.0f}, Bodega: {phys_q:.0f}, Reservadas en pre-ventas: {froz_q:.0f}, Solicitado: {req_qty:.0f})."
                    )
                else:
                    raise HTTPException(
                        status_code=400,
                        detail=f"No se puede generar la pre-venta para '{prod.name}'. No cuenta con existencia disponible suficiente en bodega (Disponible: {max(0, phys_q):.0f}, Solicitado: {req_qty:.0f})."
                    )

        line_subtotal = (qty * price) - disc
        if line_subtotal < 0:
            line_subtotal = Decimal("0")

        # Asumimos precio con IVA incluido (régimen general Guatemala)
        line_tax = line_subtotal * Decimal("12") / Decimal("112")

        subtotal_accum += (line_subtotal - line_tax)
        total_accum += line_subtotal

        order_lines.append(
            SaleOrderLine(
                product_id=prod.id,
                variant_id=None,
                lot_id=None,
                uom_id=prod.uom_id,
                product_name=prod.name,
                product_barcode=prod.barcode,
                description=prod.name,
                qty=qty,
                unit_price=price,
                cost_price=prod.cost_price or Decimal("0"),
                discount_amount=disc,
                subtotal=line_subtotal - line_tax,
                tax_amount=line_tax,
                total=line_subtotal,
            )
        )

    tax_total = total_accum - subtotal_accum

    # Resolver cliente
    cust_id = None
    if payload.customer_id:
        try:
            cust_id = uuid.UUID(payload.customer_id)
        except Exception:
            pass

    # Resolver logística (sin modificar esquema de base de datos)
    delivery_dt = None
    if payload.delivery_date:
        try:
            cleaned_date = payload.delivery_date.strip()
            if "T" in cleaned_date:
                delivery_dt = datetime.fromisoformat(cleaned_date.replace("Z", "+00:00"))
            else:
                delivery_dt = datetime.strptime(cleaned_date[:10], "%Y-%m-%d").replace(tzinfo=timezone.utc)
        except Exception:
            delivery_dt = None

    delivery_addr = payload.delivery_address or payload.customer_address or "CIUDAD"
    delivery_notes = payload.delivery_notes or payload.notes or ""

    extra_payload = {
        "delivery_date": payload.delivery_date or (delivery_dt.strftime("%Y-%m-%d") if delivery_dt else None),
        "delivery_address": delivery_addr,
        "delivery_notes": delivery_notes,
        "source": "presale_portal"
    }

    presale = SaleOrder(
        company_id=company_id,
        branch_id=branch_id,
        warehouse_id=warehouse_id,
        customer_id=cust_id,
        customer_name=payload.customer_name or "CONSUMIDOR FINAL",
        customer_nit=payload.customer_nit or "CF",
        customer_address=delivery_addr,
        user_id=current_user.id,
        number=order_number,
        state=SaleState.PENDING_APPROVAL,
        sale_type=SaleType.PRESALE,
        sale_date=datetime.now(timezone.utc),
        due_date=delivery_dt,
        subtotal=subtotal_accum,
        tax_amount=tax_total,
        discount_amount=Decimal("0"),
        total=total_accum,
        amount_paid=Decimal("0"),
        amount_due=total_accum,
        notes=payload.notes or delivery_notes,
        extra=extra_payload,
        warehouse_verified=False,
    )
    presale.lines = order_lines
    db.add(presale)
    await db.commit()
    await db.refresh(presale)

    return {
        "success": True,
        "id": str(presale.id),
        "number": presale.number,
        "state": presale.state,
        "total": float(presale.total),
        "customer_name": presale.customer_name,
        "delivery_date": extra_payload.get("delivery_date"),
        "delivery_address": delivery_addr,
        "delivery_notes": delivery_notes,
        "message": "Solicitud de Pre-venta creada con éxito. Pendiente de autorización por supervisor/bodega."
    }


def _extract_delivery_info(o: SaleOrder) -> dict:
    """Extrae metadatos de logística de campos existentes de SaleOrder (extra, due_date, notes)."""
    extra = {}
    if getattr(o, "extra", None):
        if isinstance(o.extra, dict):
            extra = o.extra
        elif isinstance(o.extra, str):
            try:
                import json
                extra = json.loads(o.extra)
            except Exception:
                extra = {}

    d_date = extra.get("delivery_date")
    if not d_date and o.due_date:
        d_date = o.due_date.strftime("%Y-%m-%d")

    d_addr = extra.get("delivery_address") or o.customer_address or "CIUDAD"
    d_notes = extra.get("delivery_notes") or o.notes or ""

    return {
        "delivery_date": d_date,
        "delivery_address": d_addr,
        "delivery_notes": d_notes
    }


# ===== 3. LISTAR SOLICITUDES POS (PANEL ADMINISTRATIVO / BODEGA) =====
@router.get("/solicitudes")
async def list_solicitudes_pos(
    state: Optional[str] = Query(None, description="pending_approval, approved, rejected, etc."),
    search: Optional[str] = Query(None),
    branch_id: Optional[str] = Query(None),
    limit: int = Query(50, ge=1, le=200),
    offset: int = Query(0, ge=0),
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    """Lista todas las solicitudes de Pre-venta con datos de preventista, cliente, logística y bodega."""
    company_id = current_user.company_id
    await auto_expire_stale_presales(db, company_id)

    query = select(SaleOrder).where(
        and_(
            SaleOrder.company_id == company_id,
            SaleOrder.sale_type == SaleType.PRESALE
        )
    ).options(
        selectinload(SaleOrder.lines),
        selectinload(SaleOrder.user),
        selectinload(SaleOrder.approver),
        selectinload(SaleOrder.warehouse),
        selectinload(SaleOrder.branch),
    ).order_by(desc(SaleOrder.created_at))

    if state:
        query = query.where(SaleOrder.state == state)
    if branch_id:
        try:
            b_uuid = uuid.UUID(branch_id)
            query = query.where(SaleOrder.branch_id == b_uuid)
        except Exception:
            pass
    if search:
        s = f"%{search.strip()}%"
        query = query.where(
            (SaleOrder.number.ilike(s)) |
            (SaleOrder.customer_name.ilike(s)) |
            (SaleOrder.customer_nit.ilike(s))
        )

    # Conteo total
    count_stmt = select(func.count()).select_from(query.subquery())
    total_count = (await db.execute(count_stmt)).scalar() or 0

    # Paginación
    query = query.limit(limit).offset(offset)
    res = await db.execute(query)
    orders = res.scalars().all()

    items = []
    for o in orders:
        delivery_info = _extract_delivery_info(o)
        items.append({
            "id": str(o.id),
            "number": o.number,
            "created_at": _iso_utc(o.created_at),
            "sale_date": _iso_utc(o.sale_date),
            "state": o.state,
            "customer_name": o.customer_name,
            "customer_nit": o.customer_nit,
            "total": float(o.total or 0),
            "items_count": len(o.lines or []),
            "preventista": {
                "id": str(o.user.id) if o.user else None,
                "name": o.user.full_name if o.user else "N/A",
                "username": o.user.username if o.user else "N/A",
            } if o.user else None,
            "approver": {
                "id": str(o.approver.id) if o.approver else None,
                "name": o.approver.full_name if o.approver else "N/A",
            } if o.approver else None,
            "approved_at": o.approved_at.isoformat() if o.approved_at else None,
            "rejection_reason": o.rejection_reason,
            "warehouse_name": o.warehouse.name if o.warehouse else "Bodega Principal",
            "branch_name": o.branch.name if o.branch else "Sucursal",
            "warehouse_verified": o.warehouse_verified,
            "delivery_date": delivery_info["delivery_date"],
            "delivery_address": delivery_info["delivery_address"],
            "delivery_notes": delivery_info["delivery_notes"],
        })

    return {
        "total": total_count,
        "items": items,
        "limit": limit,
        "offset": offset,
    }


# ===== 4. MIS SOLICITUDES (PREVENTISTA) =====
@router.get("/my-requests")
async def get_my_requests(
    limit: int = Query(30, ge=1, le=100),
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    """Devuelve las solicitudes emitidas por el usuario preventista actual."""
    await auto_expire_stale_presales(db, current_user.company_id)
    query = select(SaleOrder).where(
        and_(
            SaleOrder.company_id == current_user.company_id,
            SaleOrder.user_id == current_user.id,
            SaleOrder.sale_type == SaleType.PRESALE
        )
    ).options(
        selectinload(SaleOrder.lines),
        selectinload(SaleOrder.warehouse)
    ).order_by(desc(SaleOrder.created_at)).limit(limit)

    res = await db.execute(query)
    orders = res.scalars().all()

    items = []
    for o in orders:
        delivery_info = _extract_delivery_info(o)
        items.append({
            "id": str(o.id),
            "number": o.number,
            "created_at": _iso_utc(o.created_at),
            "state": o.state,
            "customer_name": o.customer_name,
            "total": float(o.total or 0),
            "items_count": len(o.lines or []),
            "rejection_reason": o.rejection_reason,
            "warehouse_name": o.warehouse.name if o.warehouse else "Bodega Principal",
            "delivery_date": delivery_info["delivery_date"],
            "delivery_address": delivery_info["delivery_address"],
            "delivery_notes": delivery_info["delivery_notes"],
        })

    return items


# ===== 5. DETALLE Y VALIDACIÓN DE EXISTENCIAS =====
@router.get("/{order_id}")
async def get_presale_detail(
    order_id: str,
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    """Detalle completo de la solicitud POS con validación de stock por línea."""
    oid = uuid.UUID(order_id) if isinstance(order_id, str) else order_id
    query = select(SaleOrder).where(
        and_(
            SaleOrder.id == oid,
            SaleOrder.company_id == current_user.company_id
        )
    ).options(
        selectinload(SaleOrder.lines).selectinload(SaleOrderLine.product),
        selectinload(SaleOrder.user),
        selectinload(SaleOrder.approver),
        selectinload(SaleOrder.warehouse),
        selectinload(SaleOrder.branch),
    )
    res = await db.execute(query)
    order = res.scalars().first()
    if not order:
        raise HTTPException(status_code=404, detail="Solicitud de pre-venta no encontrada.")

    # Analizar existencias en la bodega asignada
    lines_detail = []
    all_available = True

    for l in order.lines:
        # Consultar stock actual en la bodega seleccionada
        wh_stock_stmt = select(func.coalesce(StockQuant.qty_on_hand, 0)).where(
            and_(
                StockQuant.warehouse_id == order.warehouse_id,
                StockQuant.product_id == l.product_id
            )
        )
        wh_stock = (await db.execute(wh_stock_stmt)).scalar() or 0

        req_qty = float(l.qty or 0)
        has_stock = float(wh_stock) >= req_qty
        if not l.is_deleted and not has_stock:
            all_available = False

        lines_detail.append({
            "id": str(l.id),
            "product_id": str(l.product_id),
            "product_name": l.product_name or (l.product.name if l.product else "N/A"),
            "barcode": l.product_barcode or (l.product.barcode if l.product else ""),
            "sku": getattr(l.product, "internal_ref", "") if l.product else "",
            "image_url": l.product.image_url if l.product else None,
            "quantity": req_qty,
            "unit_price": float(l.unit_price or 0),
            "discount_amount": float(l.discount_amount or 0),
            "total": float(l.total or 0),
            "warehouse_stock": float(wh_stock),
            "is_available": has_stock,
            "stock_difference": float(wh_stock) - req_qty,
            "is_deleted": bool(l.is_deleted),
        })

    delivery_info = _extract_delivery_info(order)

    return {
        "id": str(order.id),
        "number": order.number,
        "created_at": _iso_utc(order.created_at),
        "state": order.state,
        "customer_name": order.customer_name,
        "customer_nit": order.customer_nit,
        "customer_address": order.customer_address,
        "subtotal": float(order.subtotal or 0),
        "tax_amount": float(order.tax_amount or 0),
        "total": float(order.total or 0),
        "notes": order.notes,
        "internal_notes": order.internal_notes,
        "extra": order.extra,
        "warehouse_id": str(order.warehouse_id),
        "warehouse_name": order.warehouse.name if order.warehouse else "Bodega Principal",
        "branch_id": str(order.branch_id),
        "branch_name": order.branch.name if order.branch else "Sucursal",
        "delivery_date": delivery_info["delivery_date"],
        "delivery_address": delivery_info["delivery_address"],
        "delivery_notes": delivery_info["delivery_notes"],
        "preventista": {
            "name": order.user.full_name if order.user else "N/A",
            "username": order.user.username if order.user else "N/A",
        } if order.user else None,
        "approver": {
            "name": order.approver.full_name if order.approver else "N/A",
        } if order.approver else None,
        "approved_at": order.approved_at.isoformat() if order.approved_at else None,
        "rejection_reason": order.rejection_reason,
        "warehouse_verified": order.warehouse_verified,
        "all_available": all_available,
        "lines": lines_detail,
    }


# ===== 6. VALIDAR CON BODEGA =====
@router.post("/{order_id}/validate-stock")
async def validate_stock_with_warehouse(
    order_id: str,
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    """Marca la solicitud como revisada con bodega y actualiza el estado de verificación."""
    oid = uuid.UUID(order_id) if isinstance(order_id, str) else order_id
    query = select(SaleOrder).where(
        and_(SaleOrder.id == oid, SaleOrder.company_id == current_user.company_id)
    ).options(selectinload(SaleOrder.lines).selectinload(SaleOrderLine.product))
    res = await db.execute(query)
    order = res.scalars().first()
    if not order:
        raise HTTPException(status_code=404, detail="Solicitud no encontrada.")

    all_ok = True
    report = []
    for l in order.lines:
        stk_stmt = select(func.coalesce(StockQuant.qty_on_hand, 0)).where(
            and_(StockQuant.warehouse_id == order.warehouse_id, StockQuant.product_id == l.product_id)
        )
        avail = (await db.execute(stk_stmt)).scalar() or 0

        req_qty = float(l.qty or 0)
        is_ok = float(avail) >= req_qty
        if not is_ok:
            all_ok = False
        report.append({
            "product_name": l.product_name,
            "requested": req_qty,
            "available": float(avail),
            "ok": is_ok,
        })

    order.warehouse_verified = True
    if order.state == SaleState.PENDING_APPROVAL:
        order.state = SaleState.WAREHOUSE_VALIDATION
    await db.commit()

    return {
        "success": True,
        "all_available": all_ok,
        "warehouse_verified": True,
        "report": report,
        "message": "Validación con bodega completada."
    }


# ===== 6.1 FASE 1: APROBAR SOLICITUD PARA PREPARACIÓN (SIN DESCONTAR INVENTARIO) =====
@router.post("/{order_id}/pre-approve")
async def pre_approve_presale_order(
    order_id: str,
    payload: Optional[PresaleApproveIn] = None,
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    """
    FASE 1: APROBACIÓN INICIAL PARA PREPARACIÓN EN BODEGA (SIN DESCONTAR INVENTARIO).
    - Cambia estado a WAREHOUSE_VALIDATION.
    - Registra approved_by_id y approved_at.
    - warehouse_verified = False (pendiente de que el bodeguero lo prepare).
    - El inventario físico NO se descuenta aún (permanece congelado/reservado).
    """
    oid = uuid.UUID(order_id) if isinstance(order_id, str) else order_id
    query = select(SaleOrder).where(
        and_(SaleOrder.id == oid, SaleOrder.company_id == current_user.company_id)
    ).options(
        selectinload(SaleOrder.lines).selectinload(SaleOrderLine.product),
        selectinload(SaleOrder.warehouse)
    )
    res = await db.execute(query)
    order = res.scalars().first()
    if not order:
        raise HTTPException(status_code=404, detail="Solicitud no encontrada.")

    if order.state in [SaleState.APPROVED, SaleState.CONFIRMED, SaleState.INVOICED, SaleState.PAID]:
        raise HTTPException(status_code=400, detail="Esta solicitud ya fue autorizada y finalizada previamente.")

    if order.state == SaleState.REJECTED:
        raise HTTPException(status_code=400, detail="No se puede aprobar una solicitud rechazada.")

    if payload and payload.warehouse_id:
        try:
            order.warehouse_id = uuid.UUID(payload.warehouse_id)
        except Exception:
            pass

    order.state = SaleState.WAREHOUSE_VALIDATION
    order.approved_by_id = current_user.id
    order.approved_at = datetime.now(timezone.utc)
    order.warehouse_verified = False
    if payload and payload.internal_notes:
        order.internal_notes = payload.internal_notes

    await db.commit()
    await db.refresh(order)

    return {
        "success": True,
        "id": str(order.id),
        "number": order.number,
        "state": order.state,
        "warehouse_verified": False,
        "message": f"¡Solicitud {order.number} aprobada! Ha sido enviada al módulo de Preparación en Bodega.",
    }


# ===== 6.2 LISTADO PARA MÓDULO DE PREPARACIÓN EN BODEGA =====
@router.get("/preparation/orders")
async def get_preparation_orders(
    warehouse_id: Optional[str] = Query(None),
    status: Optional[str] = Query(None),
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    """
    Retorna las órdenes aprobadas pendientes de preparación física por el bodeguero.
    """
    await auto_expire_stale_presales(db, current_user.company_id)

    query = select(SaleOrder).where(
        SaleOrder.company_id == current_user.company_id,
        SaleOrder.sale_type == SaleType.PRESALE,
        SaleOrder.state == SaleState.WAREHOUSE_VALIDATION,
        SaleOrder.is_deleted == False
    ).options(
        selectinload(SaleOrder.lines).selectinload(SaleOrderLine.product).selectinload(Product.uom),
        selectinload(SaleOrder.warehouse),
        selectinload(SaleOrder.branch),
        selectinload(SaleOrder.user)
    ).order_by(SaleOrder.created_at.desc())

    if warehouse_id and isinstance(warehouse_id, str) and warehouse_id.strip():
        try:
            query = query.where(SaleOrder.warehouse_id == uuid.UUID(warehouse_id.strip()))
        except Exception:
            pass

    res = await db.execute(query)
    orders = res.scalars().all()

    items = []
    for o in orders:
        is_verified = bool(o.warehouse_verified)
        if status == "pending" and is_verified:
            continue
        if status == "prepared" and not is_verified:
            continue

        deliv = _extract_delivery_info(o)
        order_lines = []
        for l in o.lines:
            order_lines.append({
                "id": str(l.id),
                "product_id": str(l.product_id),
                "name": l.product_name or (l.product.name if l.product else "N/A"),
                "sku": getattr(l.product, "internal_ref", "") if l.product else "",
                "barcode": l.product_barcode or (l.product.barcode if l.product else ""),
                "qty": float(l.qty or 0),
                "uom": l.product.uom.symbol if (l.product and l.product.uom) else "und",
                "image_url": l.product.image_url if l.product else None,
            })

        items.append({
            "id": str(o.id),
            "number": o.number,
            "created_at": _iso_utc(o.created_at),
            "approved_at": _iso_utc(o.approved_at),
            "state": o.state,
            "warehouse_verified": is_verified,
            "customer_name": o.customer_name or "Consumidor Final",
            "customer_phone": o.customer_phone if hasattr(o, 'customer_phone') else "",
            "delivery_address": deliv.get("delivery_address", "") or o.customer_address or "Recoge en Tienda / Bodega",
            "delivery_date": deliv.get("delivery_date", ""),
            "delivery_notes": deliv.get("delivery_notes", ""),
            "notes": o.notes or "",
            "total": float(o.total or 0),
            "total_items": sum(l["qty"] for l in order_lines),
            "warehouse_id": str(o.warehouse_id),
            "warehouse_name": o.warehouse.name if o.warehouse else "Bodega Central",
            "seller_name": o.user.full_name if o.user else "Preventista",
            "lines": order_lines,
        })

    return {
        "items": items,
        "total": len(items),
        "pending_count": sum(1 for x in items if not x["warehouse_verified"]),
        "prepared_count": sum(1 for x in items if x["warehouse_verified"]),
    }


# ===== 6.3 BODEGUERO MARCA COMO PRODUCTO PREPARADO =====
@router.post("/{order_id}/mark-prepared")
async def mark_presale_order_prepared(
    order_id: str,
    payload: Optional[PresaleMarkPreparedIn] = None,
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    """
    FASE 2: EL BODEGUERO MARCA EL PRODUCTO COMO PREPARADO / LISTO PARA DESPACHO.
    Si se desmarcaron productos o se ajustaron cantidades reales en bodega:
    - Se eliminan/desactivan las líneas no preparadas (is_deleted = True).
    - Se ajustan las cantidades preparadas reales.
    - Se recalculan los totales del pedido para que solo se cobre y despache lo preparado.
    - Se guarda el detalle de preparación en order.extra e internal_notes.
    """
    oid = uuid.UUID(order_id) if isinstance(order_id, str) else order_id
    query = (
        select(SaleOrder)
        .where(and_(SaleOrder.id == oid, SaleOrder.company_id == current_user.company_id))
        .options(selectinload(SaleOrder.lines).selectinload(SaleOrderLine.product))
    )
    res = await db.execute(query)
    order = res.scalars().first()
    if not order:
        raise HTTPException(status_code=404, detail="Solicitud no encontrada.")

    if order.state == SaleState.REJECTED:
        raise HTTPException(status_code=400, detail="La solicitud está rechazada.")

    unprepared_names = []
    has_partial = False

    if payload and payload.items:
        items_map = {}
        for it in payload.items:
            if it.line_id:
                items_map[str(it.line_id)] = it
            if it.product_id:
                items_map[str(it.product_id)] = it

        for line in order.lines:
            line_key = str(line.id)
            prod_key = str(line.product_id)
            match_it = items_map.get(line_key) or items_map.get(prod_key)

            if match_it:
                if not match_it.is_prepared or (match_it.qty_prepared is not None and match_it.qty_prepared <= 0):
                    # Producto NO preparado / NO disponible en bodega
                    has_partial = True
                    unprepared_names.append(line.product_name)
                    line.is_deleted = True
                    line.qty = Decimal("0")
                    line.subtotal = Decimal("0")
                    line.tax_amount = Decimal("0")
                    line.total = Decimal("0")
                elif match_it.qty_prepared is not None and match_it.qty_prepared < float(line.qty):
                    # Preparación parcial de cantidad
                    has_partial = True
                    line.qty = Decimal(str(match_it.qty_prepared))
                    line.compute_totals()
                else:
                    line.is_deleted = False

        active_lines = [l for l in order.lines if not l.is_deleted and l.qty > 0]
        if not active_lines:
            raise HTTPException(
                status_code=400,
                detail="No se marcó ningún producto como preparado. Si no hay existencia física en bodega, puedes rechazar la solicitud."
            )

        # Recalcular totales de la orden
        order.subtotal = sum(l.subtotal for l in active_lines)
        order.tax_amount = sum(l.tax_amount for l in active_lines)
        order.total = sum(l.total for l in active_lines)

    # Registrar en extra e internal_notes
    extra_data = dict(order.extra or {})
    extra_data["warehouse_prep"] = {
        "prepared_at": datetime.now(timezone.utc).isoformat(),
        "prepared_by": current_user.full_name or current_user.email,
        "is_partial": has_partial,
        "unprepared_items": unprepared_names,
        "notes": payload.notes if payload else None,
    }
    order.extra = extra_data

    if has_partial and unprepared_names:
        note_msg = f"[Bodega]: Preparación con faltantes. Productos no disponibles omitidos: {', '.join(unprepared_names)}. Total ajustado a Q {float(order.total):.2f}."
        if order.internal_notes:
            order.internal_notes = f"{order.internal_notes}\n{note_msg}"
        else:
            order.internal_notes = note_msg

    order.warehouse_verified = True
    await db.commit()
    await db.refresh(order)

    msg = f"¡Pedido {order.number} marcado como PREPARADO por el bodeguero! Listo para autorizar despacho."
    if has_partial:
        msg = f"¡Pedido {order.number} preparado con ajuste! Se omitieron {len(unprepared_names)} producto(s) no disponible(s). Total ajustado a Q {float(order.total):.2f}."

    return {
        "success": True,
        "id": str(order.id),
        "number": order.number,
        "warehouse_verified": True,
        "total": float(order.total),
        "is_partial": has_partial,
        "message": msg,
    }


@router.post("/{order_id}/unmark-prepared")
async def unmark_presale_order_prepared(
    order_id: str,
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    """Regresar pedido a pendiente de preparación."""
    oid = uuid.UUID(order_id) if isinstance(order_id, str) else order_id
    query = select(SaleOrder).where(
        and_(SaleOrder.id == oid, SaleOrder.company_id == current_user.company_id)
    )
    res = await db.execute(query)
    order = res.scalars().first()
    if not order:
        raise HTTPException(status_code=404, detail="Solicitud no encontrada.")

    order.warehouse_verified = False
    await db.commit()
    await db.refresh(order)

    return {
        "success": True,
        "id": str(order.id),
        "warehouse_verified": False,
        "message": f"Pedido {order.number} devuelto a pendiente de preparación.",
    }


# ===== 7. APROBAR FINAL / DESPACHAR Y REBAJAR INVENTARIO =====
@router.post("/{order_id}/dispatch")
@router.post("/{order_id}/approve")
async def approve_presale_order(
    order_id: str,
    payload: Optional[PresaleApproveIn] = None,
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    """
    AUTORIZACIÓN FINAL DE LA SOLICITUD POS:
    1. Rebaja existencias físicas en la bodega (StockQuant).
    2. Rebaja existencias en el catálogo de productos (Product.stock).
    3. Registra movimientos en Kardex (StockMoveType.SALE).
    4. Cambia estado a APPROVED y registra usuario autorizador.
    """
    oid = uuid.UUID(order_id) if isinstance(order_id, str) else order_id
    query = select(SaleOrder).where(
        and_(SaleOrder.id == oid, SaleOrder.company_id == current_user.company_id)
    ).options(
        selectinload(SaleOrder.lines).selectinload(SaleOrderLine.product),
        selectinload(SaleOrder.warehouse)
    )
    res = await db.execute(query)
    order = res.scalars().first()
    if not order:
        raise HTTPException(status_code=404, detail="Solicitud no encontrada.")

    if order.state in [SaleState.APPROVED, SaleState.CONFIRMED, SaleState.INVOICED, SaleState.PAID]:
        raise HTTPException(status_code=400, detail="Esta solicitud ya fue autorizada previamente.")

    if order.state == SaleState.REJECTED:
        raise HTTPException(status_code=400, detail="No se puede aprobar una solicitud rechazada.")

    # Bodega a despachar
    target_wh_id = order.warehouse_id
    if payload and payload.warehouse_id:
        try:
            target_wh_id = uuid.UUID(payload.warehouse_id)
            order.warehouse_id = target_wh_id
        except Exception:
            pass

    for line in order.lines:
        if line.is_deleted:
            continue
        qty_to_deduct = Decimal(str(line.qty or 0))
        if qty_to_deduct <= 0:
            continue
        p_id = line.product_id

        # 1. Actualizar o crear StockQuant para la bodega
        quant_stmt = select(StockQuant).where(
            and_(StockQuant.warehouse_id == target_wh_id, StockQuant.product_id == p_id)
        )
        quant = (await db.execute(quant_stmt)).scalars().first()
        qty_before = quant.qty_on_hand if quant else Decimal("0")
        if qty_before < qty_to_deduct:
            p_label = line.product_name or (line.product.name if line.product else 'Producto')
            raise HTTPException(
                status_code=400,
                detail=f"No se puede autorizar el despacho de '{p_label}'. Stock insuficiente en bodega (Existencia física actual: {qty_before:.0f}, Solicitado a despachar: {qty_to_deduct:.0f})."
            )
        qty_after = qty_before - qty_to_deduct

        if quant:
            quant.qty_on_hand = qty_after
        else:
            quant = StockQuant(
                warehouse_id=target_wh_id,
                product_id=p_id,
                qty_on_hand=qty_after,
            )
            db.add(quant)

        # 2. Registrar movimiento en Kardex (StockMove)
        unit_cost = line.cost_price or Decimal("0")
        stock_move = StockMove(
            warehouse_id=target_wh_id,
            product_id=p_id,
            move_type=StockMoveType.SALE,
            reference=order.number,
            reference_id=order.id,
            qty=qty_to_deduct,
            qty_before=qty_before,
            qty_after=qty_after,
            unit_cost=unit_cost,
            total_cost=unit_cost * qty_to_deduct,
            notes=f"Despacho Pre-venta autorizada: {order.number} (Cliente: {order.customer_name})",
            user_id=current_user.id,
        )
        db.add(stock_move)

    # 4. Actualizar estado de la orden
    order.state = SaleState.APPROVED
    order.approved_by_id = current_user.id
    order.approved_at = datetime.now(timezone.utc)
    order.warehouse_verified = True
    if payload and payload.internal_notes:
        order.internal_notes = payload.internal_notes

    # 5. Si la pre-venta pertenece a un cliente, registrar/actualizar su cuenta por cobrar
    if order.customer_id:
        cust_stmt = select(Partner).where(
            Partner.id == order.customer_id,
            Partner.company_id == current_user.company_id
        )
        cust_res = await db.execute(cust_stmt)
        cust = cust_res.scalar_one_or_none()
        if cust:
            order_total = Decimal(str(order.total or 0))
            cust.balance = (cust.balance or Decimal("0")) + order_total

            order_dt = order.sale_date or order.created_at or datetime.now(timezone.utc)
            credit_days = cust.payment_days or 30
            due_date = order.due_date or (order_dt + timedelta(days=credit_days))

            inv_stmt = select(CustomerInvoiceCredit).where(
                CustomerInvoiceCredit.sale_id == order.id,
                CustomerInvoiceCredit.company_id == current_user.company_id,
                CustomerInvoiceCredit.is_deleted == False
            )
            existing_inv = (await db.execute(inv_stmt)).scalar_one_or_none()
            if not existing_inv:
                credit_inv = CustomerInvoiceCredit(
                    company_id=current_user.company_id,
                    customer_id=cust.id,
                    sale_id=order.id,
                    number=order.number,
                    issue_date=order_dt,
                    due_date=due_date,
                    total=order_total,
                    balance=order_total,
                    state="open",
                    notes=f"Pre-venta {order.number} - Cliente: {cust.name}"
                )
                db.add(credit_inv)
            else:
                existing_inv.total = order_total
                existing_inv.balance = order_total
                existing_inv.state = "open"

    await db.commit()
    await db.refresh(order)

    return {
        "success": True,
        "id": str(order.id),
        "number": order.number,
        "state": order.state,
        "approved_at": order.approved_at.isoformat(),
        "message": f"¡Solicitud {order.number} autorizada con éxito! El inventario ha sido rebajado de la bodega.",
    }


# ===== 8. RECHAZAR SOLICITUD =====
@router.post("/{order_id}/reject")
async def reject_presale_order(
    order_id: str,
    payload: PresaleRejectIn,
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    """Rechaza la solicitud de pre-venta sin modificar inventario."""
    if not payload.reason or not payload.reason.strip():
        raise HTTPException(status_code=400, detail="Debe indicar el motivo del rechazo.")

    oid = uuid.UUID(order_id) if isinstance(order_id, str) else order_id
    query = select(SaleOrder).where(
        and_(SaleOrder.id == oid, SaleOrder.company_id == current_user.company_id)
    )
    res = await db.execute(query)
    order = res.scalars().first()
    if not order:
        raise HTTPException(status_code=404, detail="Solicitud no encontrada.")

    if order.state == SaleState.APPROVED:
        raise HTTPException(status_code=400, detail="Esta solicitud ya fue aprobada y rebajada de inventario.")

    order.state = SaleState.REJECTED
    order.rejection_reason = payload.reason.strip()
    order.approved_by_id = current_user.id
    order.approved_at = datetime.now(timezone.utc)

    # Anular cuenta por cobrar asociada si existiera
    inv_stmt = select(CustomerInvoiceCredit).where(
        CustomerInvoiceCredit.sale_id == order.id,
        CustomerInvoiceCredit.company_id == current_user.company_id,
        CustomerInvoiceCredit.is_deleted == False
    )
    existing_invs = (await db.execute(inv_stmt)).scalars().all()
    for inv in existing_invs:
        inv.is_deleted = True
        inv.state = "cancelled"

    await db.commit()

    return {
        "success": True,
        "id": str(order.id),
        "number": order.number,
        "state": order.state,
        "rejection_reason": order.rejection_reason,
        "message": f"Solicitud {order.number} rechazada."
    }
