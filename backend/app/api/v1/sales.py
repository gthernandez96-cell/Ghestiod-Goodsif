"""API v1 — Ventas / POS."""
from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy import select, and_, func
from sqlalchemy.orm import selectinload
from pydantic import BaseModel
from typing import Optional, List
from decimal import Decimal
from datetime import datetime, timezone
import uuid

from app.core.database import get_db
from app.core.security import get_current_user
from app.models.sale import SaleOrder, SaleOrderLine, SalePayment, SaleState, SaleType
from app.models.cashier import CashierSession, SessionState, CashMovementType, PaymentMethod, CashMovement
from app.models.inventory import StockMoveType
from app.services.inventory_service import InventoryService
from app.services.fel_service import FELService

router = APIRouter(prefix="/sales")


# ===== SCHEMAS =====
class SaleLineIn(BaseModel):
    product_id: str
    variant_id: Optional[str] = None
    lot_id: Optional[str] = None
    product_name: str
    product_barcode: Optional[str] = None
    qty: Decimal
    unit_price: Decimal
    cost_price: Decimal = Decimal("0")
    discount_pct: Decimal = Decimal("0")
    tax_rate: Decimal = Decimal("12")


class SalePaymentIn(BaseModel):
    payment_method: str = "cash"
    amount: Decimal
    reference: Optional[str] = None
    card_last4: Optional[str] = None


class CreateSaleIn(BaseModel):
    branch_id: str
    warehouse_id: str
    cashier_session_id: Optional[str] = None
    customer_id: Optional[str] = None
    customer_nit: Optional[str] = "CF"
    customer_name: Optional[str] = "CONSUMIDOR FINAL"
    price_list_id: Optional[str] = None
    sale_type: str = "pos"
    lines: List[SaleLineIn]
    payments: List[SalePaymentIn]
    notes: Optional[str] = None
    generate_fel: bool = True


# ===== HELPERS =====
def _generate_sale_number(branch_code: str) -> str:
    """Generar número de venta único."""
    now = datetime.now()
    seq = str(uuid.uuid4().int)[:8]
    return f"{branch_code}-{now.strftime('%Y%m%d')}-{seq}"


# ===== ENDPOINTS =====
@router.post("/")
async def create_sale(
    data: CreateSaleIn,
    db: AsyncSession = Depends(get_db),
    current_user=Depends(get_current_user),
):
    """Crear venta completa desde el POS."""
    inv_service = InventoryService(db)

    # Crear la orden
    sale = SaleOrder(
        company_id=current_user.company_id,
        branch_id=data.branch_id,
        warehouse_id=data.warehouse_id,
        customer_id=data.customer_id,
        cashier_session_id=data.cashier_session_id,
        user_id=str(current_user.id),
        price_list_id=data.price_list_id,
        number=_generate_sale_number("POS"),
        state=SaleState.DRAFT,
        sale_type=SaleType(data.sale_type),
        sale_date=datetime.now(timezone.utc),
        customer_nit=data.customer_nit,
        customer_name=data.customer_name,
        notes=data.notes,
    )

    # Agregar líneas y calcular totales
    subtotal = Decimal("0")
    tax_amount = Decimal("0")
    discount_amount = Decimal("0")

    for line_data in data.lines:
        line = SaleOrderLine(
            product_id=line_data.product_id,
            variant_id=line_data.variant_id,
            lot_id=line_data.lot_id,
            product_name=line_data.product_name,
            product_barcode=line_data.product_barcode,
            qty=line_data.qty,
            unit_price=line_data.unit_price,
            cost_price=line_data.cost_price,
            discount_pct=line_data.discount_pct,
            tax_rate=line_data.tax_rate,
        )
        line.compute_totals()
        sale.lines.append(line)

        subtotal += line.subtotal
        tax_amount += line.tax_amount
        discount_amount += line.discount_amount

    sale.subtotal = subtotal
    sale.tax_amount = tax_amount
    sale.discount_amount = discount_amount
    sale.total = subtotal + tax_amount

    # Agregar pagos
    amount_paid = Decimal("0")
    for pay_data in data.payments:
        payment = SalePayment(
            payment_method=pay_data.payment_method,
            amount=pay_data.amount,
            reference=pay_data.reference,
            card_last4=pay_data.card_last4,
            session_id=data.cashier_session_id,
        )
        sale.payments.append(payment)
        amount_paid += pay_data.amount

    sale.amount_paid = amount_paid
    sale.amount_due = max(Decimal("0"), sale.total - amount_paid)

    # Cambiar estado
    sale.state = SaleState.PAID if sale.amount_due == 0 else SaleState.CONFIRMED

    db.add(sale)
    await db.flush()

    # Descontar inventario
    for line in sale.lines:
        await inv_service.create_move(
            warehouse_id=uuid.UUID(data.warehouse_id),
            product_id=uuid.UUID(str(line.product_id)),
            move_type=StockMoveType.SALE,
            qty=line.qty,
            unit_cost=line.cost_price,
            reference=sale.number,
            reference_id=sale.id,
            lot_id=uuid.UUID(str(line.lot_id)) if line.lot_id else None,
            variant_id=uuid.UUID(str(line.variant_id)) if line.variant_id else None,
            user_id=current_user.id,
        )

    # Generar FEL
    fel_doc = None
    if data.generate_fel:
        try:
            fel_service = FELService(db, current_user.company_id)
            # Cargar relaciones necesarias para FEL
            fel_doc = await fel_service.generate_dte(sale)
            sale.fel_uuid = fel_doc.uuid
            sale.fel_number = fel_doc.number
            sale.fel_series = fel_doc.series
            sale.fel_certified_at = fel_doc.certified_at
            sale.fel_status = fel_doc.status.value
        except Exception as e:
            # No fallar la venta si FEL falla — contingencia
            import logging
            logging.getLogger(__name__).error(f"FEL Error: {e}")

    await db.commit()

    return {
        "id": str(sale.id),
        "number": sale.number,
        "state": sale.state.value,
        "total": float(sale.total),
        "amount_paid": float(sale.amount_paid),
        "amount_due": float(sale.amount_due),
        "change": float(max(Decimal("0"), amount_paid - sale.total)),
        "fel_uuid": sale.fel_uuid,
        "fel_status": sale.fel_status,
        "lines_count": len(sale.lines),
    }


@router.get("/")
async def list_sales(
    branch_id: Optional[str] = None,
    state: Optional[str] = None,
    date_from: Optional[str] = None,
    date_to: Optional[str] = None,
    search: Optional[str] = None,
    page: int = Query(1, ge=1),
    page_size: int = Query(20, ge=1, le=100),
    db: AsyncSession = Depends(get_db),
    current_user=Depends(get_current_user),
):
    """Listar ventas con filtros."""
    filters = [
        SaleOrder.company_id == current_user.company_id,
        SaleOrder.is_deleted == False,
    ]

    if branch_id:
        filters.append(SaleOrder.branch_id == branch_id)
    if state:
        filters.append(SaleOrder.state == state)
    if search:
        filters.append(
            (SaleOrder.number.ilike(f"%{search}%")) |
            (SaleOrder.customer_name.ilike(f"%{search}%")) |
            (SaleOrder.customer_nit.ilike(f"%{search}%"))
        )

    stmt = (
        select(SaleOrder)
        .where(and_(*filters))
        .order_by(SaleOrder.sale_date.desc())
        .offset((page - 1) * page_size)
        .limit(page_size)
    )

    count_stmt = select(func.count()).select_from(SaleOrder).where(and_(*filters))

    result = await db.execute(stmt)
    count_result = await db.execute(count_stmt)

    sales = result.scalars().all()
    total = count_result.scalar()

    return {
        "items": [
            {
                "id": str(s.id),
                "number": s.number,
                "state": s.state.value,
                "customer_name": s.customer_name,
                "customer_nit": s.customer_nit,
                "total": float(s.total),
                "sale_date": s.sale_date.isoformat() if s.sale_date else None,
                "fel_uuid": s.fel_uuid,
                "fel_status": s.fel_status,
            }
            for s in sales
        ],
        "total": total,
        "page": page,
        "page_size": page_size,
        "pages": (total + page_size - 1) // page_size,
    }


@router.get("/{sale_id}")
async def get_sale(
    sale_id: str,
    db: AsyncSession = Depends(get_db),
    current_user=Depends(get_current_user),
):
    """Obtener detalle de una venta."""
    result = await db.execute(
        select(SaleOrder)
        .options(selectinload(SaleOrder.lines), selectinload(SaleOrder.payments))
        .where(SaleOrder.id == sale_id, SaleOrder.is_deleted == False)
    )
    sale = result.scalar_one_or_none()
    if not sale:
        raise HTTPException(status_code=404, detail="Venta no encontrada")

    return {
        "id": str(sale.id),
        "number": sale.number,
        "state": sale.state.value,
        "sale_type": sale.sale_type.value,
        "sale_date": sale.sale_date.isoformat() if sale.sale_date else None,
        "customer_nit": sale.customer_nit,
        "customer_name": sale.customer_name,
        "subtotal": float(sale.subtotal),
        "tax_amount": float(sale.tax_amount),
        "discount_amount": float(sale.discount_amount),
        "total": float(sale.total),
        "amount_paid": float(sale.amount_paid),
        "amount_due": float(sale.amount_due),
        "fel_uuid": sale.fel_uuid,
        "fel_status": sale.fel_status,
        "notes": sale.notes,
        "lines": [
            {
                "id": str(l.id),
                "product_name": l.product_name,
                "qty": float(l.qty),
                "unit_price": float(l.unit_price),
                "discount_pct": float(l.discount_pct),
                "subtotal": float(l.subtotal),
                "tax_amount": float(l.tax_amount),
                "total": float(l.total),
            }
            for l in sale.lines
        ],
        "payments": [
            {
                "payment_method": p.payment_method,
                "amount": float(p.amount),
                "reference": p.reference,
            }
            for p in sale.payments
        ],
    }


@router.post("/{sale_id}/cancel")
async def cancel_sale(
    sale_id: str,
    db: AsyncSession = Depends(get_db),
    current_user=Depends(get_current_user),
):
    """Cancelar una venta y revertir inventario."""
    result = await db.execute(
        select(SaleOrder)
        .options(selectinload(SaleOrder.lines))
        .where(SaleOrder.id == sale_id)
    )
    sale = result.scalar_one_or_none()
    if not sale:
        raise HTTPException(status_code=404, detail="Venta no encontrada")

    if sale.state == SaleState.CANCELLED:
        raise HTTPException(status_code=400, detail="La venta ya está cancelada")

    # Revertir movimientos de inventario
    inv_service = InventoryService(db)
    for line in sale.lines:
        await inv_service.create_move(
            warehouse_id=sale.warehouse_id,
            product_id=uuid.UUID(str(line.product_id)),
            move_type=StockMoveType.RETURN_SALE,
            qty=line.qty - line.qty_returned,
            reference=f"CANCEL-{sale.number}",
            reference_id=sale.id,
            user_id=current_user.id,
        )

    sale.state = SaleState.CANCELLED
    await db.commit()

    return {"message": f"Venta {sale.number} cancelada correctamente"}
