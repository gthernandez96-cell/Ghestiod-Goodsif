"""API v1 — Ventas / POS."""
from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy import select, and_, func
from sqlalchemy.orm import selectinload
from pydantic import BaseModel
from typing import Optional, List
from decimal import Decimal
from datetime import datetime, timezone, timedelta
import uuid

from app.core.database import get_db
from app.core.security import get_current_user
from app.models.sale import SaleOrder, SaleOrderLine, SalePayment, SaleState, SaleType
from app.models.cashier import CashierSession, SessionState, CashMovementType, PaymentMethod, CashMovement
from app.models.company import Branch, Warehouse
from app.models.product import Product, ProductType
from app.models.inventory import StockMoveType, StockQuant
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
    uom_id: Optional[str] = None
    serial_number: Optional[str] = None
    is_wholesale: Optional[bool] = False
    wholesale_ratio: Optional[Decimal] = None
    qty_in_base_units: Optional[bool] = True
    includes_tax: Optional[bool] = True


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
    client_offline_id: Optional[str] = None


# ===== HELPERS =====
def to_uuid(val):
    if not val:
        return None
    if isinstance(val, uuid.UUID):
        return val
    try:
        return uuid.UUID(str(val))
    except Exception:
        return None


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

    company_uuid = current_user.company_id if isinstance(current_user.company_id, uuid.UUID) else to_uuid(current_user.company_id)
    user_uuid = current_user.id if isinstance(current_user.id, uuid.UUID) else to_uuid(current_user.id)
    branch_uuid = to_uuid(data.branch_id)
    warehouse_uuid = to_uuid(data.warehouse_id)
    customer_uuid = to_uuid(data.customer_id)
    session_uuid = to_uuid(data.cashier_session_id)
    price_list_uuid = to_uuid(data.price_list_id)

    # Verificación de idempotencia para ventas offline reintentadas
    if data.client_offline_id:
        existing_stmt = select(SaleOrder).where(
            SaleOrder.company_id == company_uuid,
            SaleOrder.notes.contains(f"offline_id:{data.client_offline_id}")
        )
        existing_res = await db.execute(existing_stmt)
        existing_sale = existing_res.scalar_one_or_none()
        if existing_sale:
            return {
                "id": str(existing_sale.id),
                "number": existing_sale.number,
                "state": existing_sale.state.value if hasattr(existing_sale.state, "value") else str(existing_sale.state),
                "total": float(existing_sale.total or 0),
                "amount_paid": float(existing_sale.amount_paid or 0),
                "amount_due": float(existing_sale.amount_due or 0),
                "change": 0.0,
                "fel_uuid": existing_sale.fel_uuid,
                "fel_status": existing_sale.fel_status,
                "lines_count": len(existing_sale.lines) if existing_sale.lines else 0,
                "idempotent": True,
            }

    # Validar y asegurar branch_uuid válido perteneciente a la empresa
    valid_branch_id = None
    if branch_uuid:
        b_chk = await db.execute(
            select(Branch.id).where(Branch.id == branch_uuid, Branch.company_id == company_uuid)
        )
        valid_branch_id = b_chk.scalar_one_or_none()
        if not valid_branch_id:
            b_chk_any = await db.execute(select(Branch.id).where(Branch.id == branch_uuid))
            valid_branch_id = b_chk_any.scalar_one_or_none()

    if not valid_branch_id and session_uuid:
        s_chk = await db.execute(
            select(CashierSession.branch_id).where(CashierSession.id == session_uuid)
        )
        cand_branch = s_chk.scalar_one_or_none()
        if cand_branch:
            b_chk = await db.execute(select(Branch.id).where(Branch.id == cand_branch))
            valid_branch_id = b_chk.scalar_one_or_none()

    if not valid_branch_id:
        b_res = await db.execute(
            select(Branch.id)
            .where(Branch.company_id == company_uuid, Branch.is_deleted == False)
            .order_by(Branch.is_main.desc(), Branch.created_at.asc())
            .limit(1)
        )
        valid_branch_id = b_res.scalar_one_or_none()

    if not valid_branch_id:
        b_res = await db.execute(
            select(Branch.id).where(Branch.is_deleted == False).order_by(Branch.created_at.asc()).limit(1)
        )
        valid_branch_id = b_res.scalar_one_or_none()

    branch_uuid = valid_branch_id

    # Validar y asegurar warehouse_uuid válido perteneciente a la sucursal/empresa
    if warehouse_uuid:
        w_chk = await db.execute(
            select(Warehouse.id)
            .join(Branch, Warehouse.branch_id == Branch.id)
            .where(
                Warehouse.id == warehouse_uuid,
                Branch.company_id == company_uuid,
                Warehouse.is_active == True,
                Warehouse.is_deleted == False
            )
        )
        if not w_chk.scalar_one_or_none():
            warehouse_uuid = None

    # Si warehouse_uuid no es válido o era un ID de sucursal, buscar la bodega por defecto de la sucursal
    if not warehouse_uuid and branch_uuid:
        w_res = await db.execute(
            select(Warehouse.id)
            .where(
                Warehouse.branch_id == branch_uuid,
                Warehouse.is_active == True,
                Warehouse.is_deleted == False
            )
            .order_by(Warehouse.is_default.desc(), Warehouse.created_at.asc())
            .limit(1)
        )
        warehouse_uuid = w_res.scalar_one_or_none()

    # Si aún no hay bodega, buscar cualquier bodega activa de la empresa
    if not warehouse_uuid:
        w_res = await db.execute(
            select(Warehouse.id)
            .join(Branch, Warehouse.branch_id == Branch.id)
            .where(
                Branch.company_id == company_uuid,
                Warehouse.is_active == True,
                Warehouse.is_deleted == False
            )
            .order_by(Warehouse.is_default.desc(), Warehouse.created_at.asc())
            .limit(1)
        )
        warehouse_uuid = w_res.scalar_one_or_none()

    notes_content = data.notes or ""
    if data.client_offline_id:
        notes_content = f"[offline_id:{data.client_offline_id}] {notes_content}".strip()

    # Crear la orden
    sale = SaleOrder(
        company_id=company_uuid,
        branch_id=branch_uuid,
        warehouse_id=warehouse_uuid,
        customer_id=customer_uuid,
        cashier_session_id=session_uuid,
        user_id=user_uuid,
        price_list_id=price_list_uuid,
        number=_generate_sale_number("POS"),
        state=SaleState.DRAFT,
        sale_type=SaleType(data.sale_type),
        sale_date=datetime.now(timezone.utc),
        customer_nit=data.customer_nit,
        customer_name=data.customer_name,
        notes=notes_content,
    )

    # Validar que ningún producto sobrepase la existencia disponible en bodega ni las reservas de pre-ventas
    cutoff_7d = datetime.now(timezone.utc) - timedelta(days=7)
    for line_data in data.lines:
        p_uuid = to_uuid(line_data.product_id)
        if not p_uuid:
            continue
        prod_chk = (await db.execute(select(Product).where(Product.id == p_uuid))).scalar_one_or_none()
        if not prod_chk or getattr(prod_chk, "product_type", None) == ProductType.SERVICE:
            continue

        # 1. Stock reservado por pre-ventas pendientes (vigencia 7 días)
        froz_q = float((await db.execute(
            select(func.coalesce(func.sum(SaleOrderLine.qty), 0))
            .join(SaleOrder, SaleOrderLine.sale_id == SaleOrder.id)
            .where(
                SaleOrder.company_id == company_uuid,
                SaleOrder.sale_type == SaleType.PRESALE,
                SaleOrder.state.in_([SaleState.PENDING_APPROVAL, SaleState.WAREHOUSE_VALIDATION]),
                SaleOrder.created_at >= cutoff_7d,
                SaleOrder.is_deleted == False,
                SaleOrderLine.is_deleted == False,
                SaleOrderLine.product_id == p_uuid,
            )
        )).scalar() or 0.0)

        # 2. Stock físico real en las bodegas activas de la empresa
        phys_q = float((await db.execute(
            select(func.coalesce(func.sum(StockQuant.qty_on_hand), 0))
            .join(Warehouse, StockQuant.warehouse_id == Warehouse.id)
            .join(Branch, Warehouse.branch_id == Branch.id)
            .where(
                StockQuant.product_id == p_uuid,
                StockQuant.is_deleted == False,
                Warehouse.is_active == True,
                Warehouse.is_deleted == False,
                Branch.company_id == company_uuid,
            )
        )).scalar() or 0.0)

        avail_q = phys_q - froz_q

        # 3. Factor multiplicador si es presentación mayorista o unidad de medida
        ratio = 1.0
        if getattr(line_data, "is_wholesale", False):
            ratio = float(prod_chk.wholesale_ratio or 12.0)
        elif getattr(line_data, "uom_id", None):
            from app.models.product import UnitOfMeasure
            uom_res = await db.execute(select(UnitOfMeasure).where(UnitOfMeasure.id == to_uuid(line_data.uom_id)))
            uom = uom_res.scalar_one_or_none()
            if uom:
                ratio = float(uom.ratio or 1.0)
        req_qty = float(line_data.qty) * ratio

        if avail_q < req_qty:
            if froz_q > 0:
                raise HTTPException(
                    status_code=400,
                    detail=f"No se puede realizar la venta de '{prod_chk.name}'. El producto está reservado en pre-venta y no tiene existencia disponible suficiente (Disponible: {max(0, avail_q):.0f}, Bodega: {phys_q:.0f}, Reservadas en pre-ventas: {froz_q:.0f}, Solicitado: {req_qty:.0f})."
                )
            else:
                raise HTTPException(
                    status_code=400,
                    detail=f"No se puede realizar la venta de '{prod_chk.name}'. No cuenta con existencia disponible suficiente en bodega (Disponible: {max(0, phys_q):.0f}, Solicitado: {req_qty:.0f})."
                )

    # Agregar líneas y calcular totales
    subtotal = Decimal("0")
    tax_amount = Decimal("0")
    discount_amount = Decimal("0")

    for line_data in data.lines:
        line = SaleOrderLine(
            product_id=to_uuid(line_data.product_id),
            variant_id=to_uuid(line_data.variant_id),
            lot_id=to_uuid(line_data.lot_id),
            uom_id=to_uuid(line_data.uom_id),
            serial_number=line_data.serial_number,
            product_name=line_data.product_name,
            product_barcode=line_data.product_barcode,
            qty=line_data.qty,
            unit_price=line_data.unit_price,
            cost_price=line_data.cost_price,
            discount_pct=line_data.discount_pct,
            tax_rate=line_data.tax_rate,
        )
        inc_tax = line_data.includes_tax if line_data.includes_tax is not None else True
        line.compute_totals(includes_tax=inc_tax)
        sale.lines.append(line)

        subtotal += line.subtotal
        tax_amount += line.tax_amount
        discount_amount += line.discount_amount

    sale.subtotal = subtotal
    sale.tax_amount = tax_amount
    sale.discount_amount = discount_amount
    sale.total = sum(l.total for l in sale.lines)

    # Agregar pagos
    amount_paid = Decimal("0")
    for pay_data in data.payments:
        if pay_data.payment_method == "credit":
            if not customer_uuid:
                raise HTTPException(status_code=400, detail="Para venta a crédito debes seleccionar un cliente registrado")
            from app.models.partner import Partner
            p_res = await db.execute(select(Partner).where(Partner.id == customer_uuid))
            cust = p_res.scalar_one_or_none()
            if not cust or cust.nit == "CF":
                raise HTTPException(status_code=400, detail="No se puede otorgar crédito a Consumidor Final (CF). Selecciona un cliente registrado.")

            c_limit = Decimal(str(cust.credit_limit or 0))
            c_balance = Decimal(str(cust.balance or 0))
            available = max(Decimal("0"), c_limit - c_balance)

            if pay_data.amount > available:
                raise HTTPException(status_code=400, detail=f"Crédito insuficiente para {cust.name}. Crédito disponible: Q {float(available):.2f}")

            cust.balance = c_balance + pay_data.amount

        raw_pay_amount = Decimal(str(pay_data.amount or 0))
        remaining_balance = max(Decimal("0"), sale.total - amount_paid)

        # En pagos en efectivo, el monto aplicado a la venta se topa al saldo restante de la orden;
        # cualquier excedente entregado por el cliente es cambio/vuelto devuelto al cliente de la gaveta.
        if pay_data.payment_method == "cash" and raw_pay_amount > remaining_balance:
            applied_amount = remaining_balance
            change_amount = raw_pay_amount - remaining_balance
            if not sale.extra:
                sale.extra = {}
            sale.extra["tendered_cash"] = float(raw_pay_amount)
            sale.extra["change_amount"] = float(change_amount)
        else:
            applied_amount = raw_pay_amount

        payment = SalePayment(
            payment_method=pay_data.payment_method,
            amount=applied_amount,
            reference=pay_data.reference,
            card_last4=pay_data.card_last4,
            session_id=session_uuid,
        )
        sale.payments.append(payment)
        amount_paid += applied_amount

    sale.amount_paid = min(amount_paid, sale.total)
    sale.amount_due = max(Decimal("0"), sale.total - sale.amount_paid)

    # Cambiar estado
    sale.state = SaleState.PAID if sale.amount_due == 0 else SaleState.CONFIRMED

    # Buscar sesión de caja para registrar venta y movimientos en efectivo
    cash_paid = sum(p.amount for p in sale.payments if p.payment_method == "cash")
    session_obj = None
    if session_uuid:
        s_res = await db.execute(select(CashierSession).where(CashierSession.id == session_uuid))
        session_obj = s_res.scalar_one_or_none()

    if not session_obj and user_uuid:
        if branch_uuid:
            s_res = await db.execute(
                select(CashierSession).where(
                    CashierSession.user_id == user_uuid,
                    CashierSession.branch_id == branch_uuid,
                    CashierSession.state == SessionState.OPEN
                ).order_by(CashierSession.opened_at.desc()).limit(1)
            )
            session_obj = s_res.scalar_one_or_none()

        if not session_obj:
            s_res = await db.execute(
                select(CashierSession).where(
                    CashierSession.user_id == user_uuid,
                    CashierSession.state == SessionState.OPEN
                ).order_by(CashierSession.opened_at.desc()).limit(1)
            )
            session_obj = s_res.scalar_one_or_none()

        if not session_obj and branch_uuid:
            s_res = await db.execute(
                select(CashierSession).where(
                    CashierSession.branch_id == branch_uuid,
                    CashierSession.state == SessionState.OPEN
                ).order_by(CashierSession.opened_at.desc()).limit(1)
            )
            session_obj = s_res.scalar_one_or_none()

    if session_obj:
        sale.cashier_session_id = session_obj.id
        for p in sale.payments:
            if not p.session_id:
                p.session_id = session_obj.id

        session_obj.total_sales = (session_obj.total_sales or Decimal("0")) + sale.total
        if cash_paid > 0:
            sale_move = CashMovement(
                session_id=session_obj.id,
                move_type=CashMovementType.SALE,
                payment_method=PaymentMethod.CASH,
                amount=cash_paid,
                reference=f"Venta {sale.number}",
                user_id=user_uuid,
            )
            db.add(sale_move)

    db.add(sale)
    await db.flush()

    # Descontar inventario
    for idx, line in enumerate(sale.lines):
        line_data = data.lines[idx] if idx < len(data.lines) else None
        if warehouse_uuid and line.product_id:
            prod_obj_res = await db.execute(select(Product).where(Product.id == to_uuid(line.product_id)))
            prod_obj = prod_obj_res.scalar_one_or_none()

            # Calcular cantidad convertida según la unidad de medida y venta mayorista
            qty_inventory = line.qty
            is_wholesale = bool(
                (line_data and getattr(line_data, "is_wholesale", False)) or
                (prod_obj and prod_obj.wholesale_uom_id and line.uom_id == prod_obj.wholesale_uom_id)
            )

            if is_wholesale:
                if line_data and getattr(line_data, "qty_in_base_units", True):
                    qty_inventory = line.qty
                else:
                    ratio = Decimal(str(
                        (line_data and getattr(line_data, "wholesale_ratio", None)) or
                        (prod_obj and prod_obj.wholesale_ratio) or
                        1
                    ))
                    qty_inventory = line.qty * ratio
            elif line.uom_id:
                from app.models.product import UnitOfMeasure
                uom_res = await db.execute(select(UnitOfMeasure).where(UnitOfMeasure.id == line.uom_id))
                uom = uom_res.scalar_one_or_none()
                if uom:
                    qty_inventory = line.qty * Decimal(str(uom.ratio))

            # Verificar si el producto es un COMBO
            if prod_obj and (prod_obj.attributes or {}).get("is_combo"):
                combo_items = (prod_obj.attributes or {}).get("combo_items", [])
                for c_item in combo_items:
                    c_prod_id = c_item.get("product_id")
                    c_qty = Decimal(str(c_item.get("qty", 1))) * line.qty
                    if c_prod_id:
                        await inv_service.create_move(
                            warehouse_id=warehouse_uuid,
                            product_id=uuid.UUID(str(c_prod_id)),
                            move_type=StockMoveType.SALE,
                            qty=c_qty,
                            reference=f"{sale.number} (Combo Componente)",
                            reference_id=sale.id,
                            user_id=user_uuid,
                        )
            else:
                await inv_service.create_move(
                    warehouse_id=warehouse_uuid,
                    product_id=to_uuid(line.product_id),
                    move_type=StockMoveType.SALE,
                    qty=qty_inventory,
                    unit_cost=line.cost_price,
                    reference=sale.number,
                    reference_id=sale.id,
                    lot_id=to_uuid(line.lot_id),
                    variant_id=to_uuid(line.variant_id),
                    user_id=user_uuid,
                    notes=f"N/S: {line.serial_number}" if line.serial_number else ""
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


class VoidSaleIn(BaseModel):
    reason: str = "Anulación o devolución solicitada por cliente"


@router.post("/{sale_id}/void")
@router.post("/{sale_id}/cancel")
async def void_or_cancel_sale(
    sale_id: str,
    data: Optional[VoidSaleIn] = None,
    db: AsyncSession = Depends(get_db),
    current_user=Depends(get_current_user),
):
    """Anular venta, certificar anulación FEL ante el SAT y revertir inventario/créditos."""
    reason_text = data.reason if data and data.reason else "Anulación autorizada en caja"
    s_uuid = to_uuid(sale_id)
    if not s_uuid:
        raise HTTPException(status_code=400, detail="ID de venta inválido")

    result = await db.execute(
        select(SaleOrder)
        .options(selectinload(SaleOrder.lines), selectinload(SaleOrder.payments))
        .where(SaleOrder.id == s_uuid)
    )
    sale = result.scalar_one_or_none()
    if not sale:
        raise HTTPException(status_code=404, detail="Venta no encontrada")

    if sale.state == SaleState.CANCELLED:
        raise HTTPException(status_code=400, detail="La venta ya fue anulada previamente")

    # 1. Revertir movimientos de inventario (incluyendo combos)
    inv_service = InventoryService(db)
    for line in sale.lines:
        prod_obj_res = await db.execute(select(Product).where(Product.id == uuid.UUID(str(line.product_id))))
        prod_obj = prod_obj_res.scalar_one_or_none()
        if prod_obj and (prod_obj.attributes or {}).get("is_combo"):
            combo_items = (prod_obj.attributes or {}).get("combo_items", [])
            for c_item in combo_items:
                c_prod_id = c_item.get("product_id")
                c_qty = Decimal(str(c_item.get("qty", 1))) * (line.qty - line.qty_returned)
                if c_prod_id:
                    await inv_service.create_move(
                        warehouse_id=sale.warehouse_id,
                        product_id=uuid.UUID(str(c_prod_id)),
                        move_type=StockMoveType.RETURN_SALE,
                        qty=c_qty,
                        reference=f"ANULA-{sale.number} (Combo)",
                        reference_id=sale.id,
                        user_id=current_user.id,
                    )
        else:
            await inv_service.create_move(
                warehouse_id=sale.warehouse_id,
                product_id=uuid.UUID(str(line.product_id)),
                move_type=StockMoveType.RETURN_SALE,
                qty=line.qty - line.qty_returned,
                reference=f"ANULA-{sale.number}",
                reference_id=sale.id,
                user_id=current_user.id,
            )

    # 2. Revertir saldo si fue a crédito
    for pay in sale.payments:
        if pay.payment_method == "credit" and sale.customer_id:
            from app.models.partner import Partner
            cust_res = await db.execute(select(Partner).where(Partner.id == sale.customer_id))
            cust = cust_res.scalar_one_or_none()
            if cust:
                cust.balance = max(Decimal("0"), Decimal(str(cust.balance or 0)) - pay.amount)

    # 3. Anular DTE en el SAT si está certificado
    fel_cancelled = False
    if sale.fel_uuid:
        try:
            from app.models.fel import FELDocument
            fel_stmt = select(FELDocument).where(FELDocument.uuid == sale.fel_uuid)
            fel_res = await db.execute(fel_stmt)
            fel_doc = fel_res.scalar_one_or_none()
            if fel_doc:
                fel_service = FELService(db, current_user.company_id)
                await fel_service.cancel_dte(fel_doc, reason_text)
                fel_cancelled = True
        except Exception as e:
            import logging
            logging.getLogger(__name__).warning(f"No se pudo anular DTE SAT: {e}")

    sale.state = SaleState.CANCELLED
    sale.fel_status = "cancelled"
    sale.notes = f"{sale.notes or ''} [ANULADA: {reason_text}]".strip()
    await db.commit()

    return {
        "status": "success",
        "message": f"Factura/Venta {sale.number} anulada con éxito.",
        "fel_cancelled": fel_cancelled,
        "stock_reversed": True,
        "reason": reason_text
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
    s_uuid = to_uuid(sale_id)
    if not s_uuid:
        raise HTTPException(status_code=400, detail="ID de venta inválido")

    result = await db.execute(
        select(SaleOrder)
        .options(selectinload(SaleOrder.lines), selectinload(SaleOrder.payments))
        .where(SaleOrder.id == s_uuid, SaleOrder.is_deleted == False)
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
