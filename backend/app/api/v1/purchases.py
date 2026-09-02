"""API v1 — Compras y Recepción de Mercadería."""
from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy import select, func
from sqlalchemy.orm import selectinload
from pydantic import BaseModel
from typing import Optional, List
from decimal import Decimal
from datetime import datetime, timezone, timedelta
import uuid

from app.core.database import get_db
from app.core.security import get_current_user
from app.models.purchase import PurchaseOrder, PurchaseOrderLine, GoodsReceipt, GoodsReceiptLine, PurchaseState
from app.models.partner import Partner
from app.models.company import Warehouse, Branch
from app.models.inventory import StockMoveType
from app.models.ap import SupplierBill
from app.services.inventory_service import InventoryService

router = APIRouter(prefix="/purchases")



# ===== SCHEMAS =====
class POLineIn(BaseModel):
    product_id: str
    product_name: str
    qty: Decimal
    unit_price: Decimal
    tax_rate: Decimal = Decimal("12.00")


class CreatePOIn(BaseModel):
    supplier_id: str
    branch_id: Optional[str] = None
    warehouse_id: Optional[str] = None
    order_date: Optional[str] = None
    expected_date: Optional[str] = None
    supplier_ref: Optional[str] = None
    lines: List[POLineIn]
    notes: Optional[str] = None
    payment_terms: Optional[str] = None


class ReceiveLineIn(BaseModel):
    line_id: str
    qty_received: Decimal


class ReceivePOIn(BaseModel):
    receipt_lines: Optional[List[ReceiveLineIn]] = None
    supplier_ref: Optional[str] = None
    received_date: Optional[str] = None
    notes: Optional[str] = None


def _generate_po_number() -> str:
    now = datetime.now()
    seq = str(uuid.uuid4().int)[:6]
    return f"OC-{now.strftime('%Y%m%d')}-{seq}"


# ===== ENDPOINTS =====
@router.get("/kpis")
async def get_purchases_kpis(
    db: AsyncSession = Depends(get_db),
    current_user=Depends(get_current_user),
):
    """Obtener métricas ejecutivas de compras y abastecimiento."""
    stmt = (
        select(PurchaseOrder)
        .options(selectinload(PurchaseOrder.lines))
        .where(PurchaseOrder.company_id == current_user.company_id)
    )
    res = await db.execute(stmt)
    purchases = res.scalars().all()

    total_spend = Decimal("0")
    pending_reception_count = 0
    received_count = 0
    total_units_received = 0.0
    supplier_ids = set()

    for po in purchases:
        state_str = po.state.value if hasattr(po.state, 'value') else str(po.state)
        total_spend += Decimal(str(po.total or 0))
        if po.supplier_id:
            supplier_ids.add(po.supplier_id)
        if state_str in ("draft", "confirmed", "partial"):
            pending_reception_count += 1
        elif state_str == "received":
            received_count += 1

        for line in getattr(po, "lines", []):
            total_units_received += float(line.qty_received or 0)

    return {
        "total_orders": len(purchases),
        "total_spend": float(total_spend),
        "pending_reception_count": pending_reception_count,
        "received_count": received_count,
        "active_suppliers_count": len(supplier_ids),
        "total_units_received": total_units_received,
    }


@router.get("/")
async def list_purchases(
    branch_id: Optional[str] = Query(None),
    supplier_id: Optional[str] = Query(None),
    db: AsyncSession = Depends(get_db),
    current_user=Depends(get_current_user),
):
    """Listar órdenes de compra."""
    stmt = (
        select(PurchaseOrder)
        .options(selectinload(PurchaseOrder.supplier))
        .where(PurchaseOrder.company_id == current_user.company_id)
        .order_by(PurchaseOrder.order_date.desc())
    )

    if branch_id:
        try:
            stmt = stmt.where(PurchaseOrder.branch_id == uuid.UUID(branch_id))
        except ValueError:
            pass

    if supplier_id:
        try:
            stmt = stmt.where(PurchaseOrder.supplier_id == uuid.UUID(supplier_id))
        except ValueError:
            pass

    res = await db.execute(stmt)
    purchases = res.scalars().all()

    return [
        {
            "id": str(po.id),
            "number": po.number,
            "supplier_id": str(po.supplier_id) if po.supplier_id else "",
            "supplier_name": po.supplier.name if po.supplier else "N/A",
            "branch_id": str(po.branch_id),
            "date": po.order_date.strftime("%d/%m/%Y") if po.order_date else "",
            "state": po.state.value if hasattr(po.state, 'value') else str(po.state),
            "total": float(po.total or 0),
            "notes": po.notes or "",
        }
        for po in purchases
    ]


@router.get("/{purchase_id}")
async def get_purchase(
    purchase_id: str,
    db: AsyncSession = Depends(get_db),
    current_user=Depends(get_current_user),
):
    """Obtener detalle de una orden de compra."""
    try:
        p_uuid = uuid.UUID(purchase_id)
    except ValueError:
        raise HTTPException(status_code=400, detail="ID de compra inválido")

    stmt = (
        select(PurchaseOrder)
        .options(selectinload(PurchaseOrder.supplier), selectinload(PurchaseOrder.lines))
        .where(PurchaseOrder.id == p_uuid, PurchaseOrder.company_id == current_user.company_id)
    )
    res = await db.execute(stmt)
    po = res.scalar_one_or_none()

    if not po:
        raise HTTPException(status_code=404, detail="Orden de compra no encontrada")

    wh_name = "Bodega Principal"
    if po.warehouse_id:
        wh_res = await db.execute(select(Warehouse).where(Warehouse.id == po.warehouse_id))
        wh_obj = wh_res.scalar_one_or_none()
        if wh_obj:
            wh_name = wh_obj.name

    return {
        "id": str(po.id),
        "number": po.number,
        "supplier_id": str(po.supplier_id) if po.supplier_id else "",
        "supplier_name": po.supplier.name if po.supplier else "N/A",
        "supplier_nit": po.supplier.nit if po.supplier else "CF",
        "supplier_phone": (po.supplier.phone or po.supplier.mobile) if po.supplier else "",
        "branch_id": str(po.branch_id),
        "warehouse_id": str(po.warehouse_id),
        "warehouse_name": wh_name,
        "state": po.state.value if hasattr(po.state, 'value') else str(po.state),
        "subtotal": float(po.subtotal or 0),
        "tax_amount": float(po.tax_amount or 0),
        "total": float(po.total or 0),
        "order_date": po.order_date.strftime("%d/%m/%Y") if po.order_date else "",
        "expected_date": po.expected_date.strftime("%d/%m/%Y") if po.expected_date else "",
        "received_date": po.received_date.strftime("%d/%m/%Y %H:%M") if po.received_date else "",
        "notes": po.notes or "",
        "supplier_ref": po.supplier_ref or "",
        "payment_terms": po.payment_terms or "contado",
        "lines": [
            {
                "id": str(line.id),
                "product_id": str(line.product_id),
                "product_name": line.product_name,
                "qty_ordered": float(line.qty_ordered or 0),
                "qty_received": float(line.qty_received or 0),
                "unit_price": float(line.unit_price or 0),
                "subtotal": float(line.subtotal or 0),
                "total": float(line.total or 0),
            }
            for line in po.lines
        ],
    }


@router.post("/")
async def create_purchase(
    data: CreatePOIn,
    db: AsyncSession = Depends(get_db),
    current_user=Depends(get_current_user),
):
    """Crear una orden de compra en estado borradores/confirmada."""
    try:
        s_uuid = uuid.UUID(data.supplier_id)
    except ValueError:
        raise HTTPException(status_code=400, detail="ID de proveedor inválido")

    b_uuid = None
    if data.branch_id:
        try:
            b_uuid = uuid.UUID(data.branch_id)
        except ValueError:
            pass
    if not b_uuid:
        b_res = await db.execute(select(Branch).where(Branch.company_id == current_user.company_id).limit(1))
        branch = b_res.scalar_one_or_none()
        b_uuid = branch.id if branch else uuid.uuid4()

    w_uuid = None
    if data.warehouse_id:
        try:
            w_uuid = uuid.UUID(data.warehouse_id)
        except ValueError:
            pass
    if not w_uuid:
        wh_stmt = select(Warehouse).join(Branch).where(Branch.company_id == current_user.company_id).limit(1)
        wh_res = await db.execute(wh_stmt)
        wh = wh_res.scalar_one_or_none()
        if wh:
            w_uuid = wh.id
        else:
            new_wh = Warehouse(branch_id=b_uuid, name="Bodega Principal", code="BOD-001", is_default=True)
            db.add(new_wh)
            await db.flush()
            w_uuid = new_wh.id

    subtotal = Decimal("0")
    tax_amount = Decimal("0")
    po_lines = []

    for item in data.lines:
        line_subtotal = item.qty * item.unit_price
        line_tax = line_subtotal * (item.tax_rate / Decimal("100"))
        line_total = line_subtotal + line_tax

        subtotal += line_subtotal
        tax_amount += line_tax

        po_line = PurchaseOrderLine(
            product_id=uuid.UUID(item.product_id),
            product_name=item.product_name,
            qty_ordered=item.qty,
            qty_pending=item.qty,
            qty_received=Decimal("0"),
            unit_price=item.unit_price,
            tax_rate=item.tax_rate,
            subtotal=line_subtotal,
            tax_amount=line_tax,
            total=line_total,
        )
        po_lines.append(po_line)

    order_dt = datetime.now(timezone.utc)
    if data.order_date:
        try:
            order_dt = datetime.fromisoformat(data.order_date.replace("Z", "+00:00"))
        except Exception:
            try:
                order_dt = datetime.strptime(data.order_date, "%Y-%m-%d")
            except Exception:
                pass

    expected_dt = None
    if data.expected_date:
        try:
            expected_dt = datetime.fromisoformat(data.expected_date.replace("Z", "+00:00"))
        except Exception:
            try:
                expected_dt = datetime.strptime(data.expected_date, "%Y-%m-%d")
            except Exception:
                pass

    po = PurchaseOrder(
        company_id=current_user.company_id,
        branch_id=b_uuid,
        warehouse_id=w_uuid,
        supplier_id=s_uuid,
        user_id=current_user.id,
        number=_generate_po_number(),
        supplier_ref=data.supplier_ref,
        state=PurchaseState.CONFIRMED,
        order_date=order_dt,
        expected_date=expected_dt,
        subtotal=subtotal,
        tax_amount=tax_amount,
        total=subtotal + tax_amount,
        notes=data.notes,
        payment_terms=data.payment_terms,
        lines=po_lines,
    )

    db.add(po)
    await db.flush()

    # Generar automáticamente la Factura en Cuentas por Pagar (AP)
    bill_num = po.supplier_ref or f"FAC-{po.number}"
    credit_days = 30
    if po.payment_terms:
        if "15" in po.payment_terms:
            credit_days = 15
        elif "30" in po.payment_terms:
            credit_days = 30
        elif "60" in po.payment_terms:
            credit_days = 60
        elif "contado" in po.payment_terms.lower() or "cash" in po.payment_terms.lower():
            credit_days = 0

    due_date = order_dt + timedelta(days=credit_days)
    supplier_bill = SupplierBill(
        company_id=current_user.company_id,
        supplier_id=po.supplier_id,
        number=bill_num,
        bill_date=order_dt,
        due_date=due_date,
        total=po.total,
        balance=po.total,
        state="open",
        notes=f"Compra {po.number}" + (f" - Ref: {po.supplier_ref}" if po.supplier_ref else "")
    )
    db.add(supplier_bill)

    await db.commit()
    await db.refresh(po)

    return {"id": str(po.id), "number": po.number, "status": "created", "total": float(po.total), "bill_id": str(supplier_bill.id)}


@router.post("/{purchase_id}/receive")
async def receive_purchase(
    purchase_id: str,
    data: ReceivePOIn,
    db: AsyncSession = Depends(get_db),
    current_user=Depends(get_current_user),
):
    """Recibir mercadería de una Orden de Compra y actualizar inventario/costos."""
    try:
        p_uuid = uuid.UUID(purchase_id)
    except ValueError:
        raise HTTPException(status_code=400, detail="ID de compra inválido")

    stmt = (
        select(PurchaseOrder)
        .options(selectinload(PurchaseOrder.lines))
        .where(PurchaseOrder.id == p_uuid, PurchaseOrder.company_id == current_user.company_id)
    )
    res = await db.execute(stmt)
    po = res.scalar_one_or_none()

    if not po:
        raise HTTPException(status_code=404, detail="Orden de compra no encontrada")

    inv_service = InventoryService(db)

    if data.supplier_ref and data.supplier_ref.strip():
        new_ref = data.supplier_ref.strip()
        po.supplier_ref = new_ref
        
        # Sincronizar número real de factura en Cuentas por Pagar si existía
        bill_stmt = select(SupplierBill).where(
            SupplierBill.company_id == current_user.company_id,
            SupplierBill.supplier_id == po.supplier_id,
            SupplierBill.notes.contains(po.number)
        )
        bill_res = await db.execute(bill_stmt)
        existing_bill = bill_res.scalar_one_or_none()
        if existing_bill and (existing_bill.number.startswith("FAC-OC-") or existing_bill.number.startswith("OC-")):
            existing_bill.number = new_ref

    # Map inputs
    recv_map = {r.line_id: r.qty_received for r in data.receipt_lines} if data.receipt_lines else {}

    all_received = True

    for line in po.lines:
        line_id_str = str(line.id)
        qty_to_recv = recv_map.get(line_id_str, line.qty_ordered - (line.qty_received or Decimal("0")))

        if qty_to_recv > 0:
            line.qty_received = (line.qty_received or Decimal("0")) + qty_to_recv
            line.qty_pending = max(Decimal("0"), line.qty_ordered - line.qty_received)

            # Crear movimiento de inventario ENTRADA
            await inv_service.create_move(
                warehouse_id=po.warehouse_id,
                product_id=line.product_id,
                move_type=StockMoveType.PURCHASE,
                qty=qty_to_recv,
                unit_cost=line.unit_price,
                reference=f"Recepción {po.number}" + (f" - Fac: {po.supplier_ref}" if po.supplier_ref else ""),
                reference_id=po.id,
                user_id=current_user.id,
            )

        if line.qty_received < line.qty_ordered:
            all_received = False

    po.state = PurchaseState.RECEIVED if all_received else PurchaseState.PARTIAL
    recv_dt = datetime.now(timezone.utc)
    if data.received_date:
        try:
            recv_dt = datetime.strptime(data.received_date, "%Y-%m-%d")
        except Exception:
            pass
    po.received_date = recv_dt

    await db.commit()
    return {"id": str(po.id), "number": po.number, "state": po.state.value, "status": "received", "supplier_ref": po.supplier_ref}


# ===== 4. GENERATE SUPPLIER BILL (AP) FROM PO =====
class CreateBillFromPOIn(BaseModel):
    bill_number: Optional[str] = None
    bill_date: Optional[str] = None
    credit_days: Optional[int] = 30
    notes: Optional[str] = None


@router.post("/{purchase_id}/create-bill")
async def create_bill_from_purchase(
    purchase_id: str,
    data: Optional[CreateBillFromPOIn] = None,
    db: AsyncSession = Depends(get_db),
    current_user=Depends(get_current_user),
):
    """Generar automáticamente una factura de Cuentas por Pagar (SupplierBill) a partir de una Orden de Compra."""
    try:
        p_uuid = uuid.UUID(purchase_id)
    except ValueError:
        raise HTTPException(status_code=400, detail="ID de compra inválido")

    stmt = (
        select(PurchaseOrder)
        .options(selectinload(PurchaseOrder.lines), selectinload(PurchaseOrder.supplier))
        .where(PurchaseOrder.id == p_uuid, PurchaseOrder.company_id == current_user.company_id)
    )
    res = await db.execute(stmt)
    po = res.scalar_one_or_none()

    if not po:
        raise HTTPException(status_code=404, detail="Orden de compra no encontrada")

    # Determinar número de factura
    req_data = data or CreateBillFromPOIn()
    bill_num = req_data.bill_number or po.supplier_ref or f"FAC-{po.number}"

    # Verificar si ya existe factura con este número para este proveedor
    exist_stmt = select(SupplierBill).where(
        SupplierBill.company_id == current_user.company_id,
        SupplierBill.supplier_id == po.supplier_id,
        SupplierBill.number == bill_num,
        SupplierBill.is_deleted == False
    )
    exist_res = await db.execute(exist_stmt)
    existing_bill = exist_res.scalar_one_or_none()
    if existing_bill:
        return {
            "status": "already_exists",
            "message": f"Ya existe una factura registrada con el No. {bill_num} para este proveedor.",
            "bill_id": str(existing_bill.id),
            "number": existing_bill.number,
            "balance": float(existing_bill.balance)
        }

    # Fechas y vencimiento
    now = datetime.now(timezone.utc)
    b_date = now
    if req_data.bill_date:
        try:
            b_date = datetime.strptime(req_data.bill_date, "%Y-%m-%d").replace(tzinfo=timezone.utc)
        except Exception:
            pass

    credit_days = req_data.credit_days if req_data.credit_days is not None else 30
    # Si la orden tenía términos de pago como 'credit_15', 'credit_30', etc.
    if po.payment_terms:
        if "15" in po.payment_terms:
            credit_days = 15
        elif "30" in po.payment_terms:
            credit_days = 30
        elif "60" in po.payment_terms:
            credit_days = 60
        elif "contado" in po.payment_terms.lower() or "cash" in po.payment_terms.lower():
            credit_days = 0

    due_date = b_date + timedelta(days=credit_days)

    bill = SupplierBill(
        company_id=current_user.company_id,
        supplier_id=po.supplier_id,
        number=bill_num,
        bill_date=b_date,
        due_date=due_date,
        total=po.total,
        balance=po.total,
        state="open",
        notes=req_data.notes or f"Generada desde Orden de Compra {po.number}" + (f" (Ref: {po.supplier_ref})" if po.supplier_ref else "")
    )
    db.add(bill)
    await db.commit()
    await db.refresh(bill)

    return {
        "status": "created",
        "message": f"Factura {bill.number} creada exitosamente en Cuentas por Pagar.",
        "bill_id": str(bill.id),
        "number": bill.number,
        "total": float(bill.total),
        "balance": float(bill.balance),
        "due_date": bill.due_date.strftime("%d/%m/%Y") if bill.due_date else ""
    }

