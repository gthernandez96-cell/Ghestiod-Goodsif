"""API v1 — Traslados Internos entre Bodegas 360°."""
from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy import select, func, and_
from sqlalchemy.orm import selectinload
from pydantic import BaseModel
from typing import Optional, List
from decimal import Decimal
from datetime import datetime, timezone
import uuid

from app.core.database import get_db
from app.core.security import get_current_user
from app.models.transfer import Transfer, TransferLine, TransferState
from app.models.company import Warehouse, Branch
from app.models.inventory import StockMoveType, StockQuant
from app.models.product import Product
from app.services.inventory_service import InventoryService

router = APIRouter(prefix="/transfers")


# ===== SCHEMAS =====
class TransferLineIn(BaseModel):
    product_id: str
    product_name: str
    qty: Decimal


class CreateTransferIn(BaseModel):
    origin_warehouse_id: str
    dest_warehouse_id: str
    origin_branch_id: Optional[str] = None
    dest_branch_id: Optional[str] = None
    lines: List[TransferLineIn]
    notes: Optional[str] = None


class SendLineIn(BaseModel):
    product_id: str
    qty_sent: Decimal


class SendTransferIn(BaseModel):
    lines: Optional[List[SendLineIn]] = None
    notes: Optional[str] = None


class ReceiveLineIn(BaseModel):
    product_id: str
    qty_received: Decimal
    notes: Optional[str] = None


class ReceiveTransferIn(BaseModel):
    lines: Optional[List[ReceiveLineIn]] = None
    notes: Optional[str] = None
    is_final: bool = True


class CancelTransferIn(BaseModel):
    reason: Optional[str] = None


def _generate_transfer_number() -> str:
    now = datetime.now()
    seq = str(uuid.uuid4().int)[:6]
    return f"TR-{now.strftime('%Y%m%d')}-{seq}"


# ===== ENDPOINTS =====
@router.get("/kpis")
async def get_transfers_kpis(
    db: AsyncSession = Depends(get_db),
    current_user=Depends(get_current_user),
):
    """Obtener métricas ejecutivas de traslados de inventario."""
    stmt = select(Transfer).where(Transfer.company_id == current_user.company_id)
    res = await db.execute(stmt)
    transfers = res.scalars().all()

    total_transfers = len(transfers)
    in_transit_count = 0
    received_count = 0
    draft_count = 0

    for t in transfers:
        state_str = t.state.value if hasattr(t.state, 'value') else str(t.state)
        if state_str in ("sent", "in_transit"):
            in_transit_count += 1
        elif state_str in ("received", "partial"):
            received_count += 1
        elif state_str in ("draft", "confirmed"):
            draft_count += 1

    wh_stmt = select(func.count(Warehouse.id)).join(Branch).where(Branch.company_id == current_user.company_id, Warehouse.is_active == True)
    wh_res = await db.execute(wh_stmt)
    active_wh_count = wh_res.scalar() or 0

    return {
        "total_transfers": total_transfers,
        "in_transit_count": in_transit_count,
        "received_count": received_count,
        "draft_count": draft_count,
        "active_warehouses_count": active_wh_count,
    }


@router.get("/warehouse-stock")
async def get_warehouse_stock(
    warehouse_id: str = Query(...),
    product_ids: Optional[str] = Query(None),
    db: AsyncSession = Depends(get_db),
    current_user=Depends(get_current_user),
):
    """Consultar existencias y costo promedio en tiempo real para una bodega."""
    try:
        w_uuid = uuid.UUID(warehouse_id)
    except ValueError:
        raise HTTPException(status_code=400, detail="ID de bodega inválido")

    stmt = select(StockQuant).where(
        StockQuant.warehouse_id == w_uuid,
        StockQuant.is_deleted == False
    )
    if product_ids:
        raw_ids = [p.strip() for p in product_ids.split(",") if p.strip()]
        valid_uuids = []
        for rid in raw_ids:
            try:
                valid_uuids.append(uuid.UUID(rid))
            except ValueError:
                pass
        if valid_uuids:
            stmt = stmt.where(StockQuant.product_id.in_(valid_uuids))

    res = await db.execute(stmt)
    quants = res.scalars().all()
    return {
        str(q.product_id): {
            "qty_on_hand": float(q.qty_on_hand or 0),
            "qty_reserved": float(q.qty_reserved or 0),
            "qty_available": float((q.qty_on_hand or 0) - (q.qty_reserved or 0)),
            "avg_cost": float(q.avg_cost or 0),
        }
        for q in quants
    }


@router.get("/")
async def list_transfers(
    branch_id: Optional[str] = Query(None),
    warehouse_id: Optional[str] = Query(None),
    db: AsyncSession = Depends(get_db),
    current_user=Depends(get_current_user),
):
    """Listar solicitudes y traslados de inventario."""
    stmt = (
        select(Transfer)
        .options(
            selectinload(Transfer.origin_branch),
            selectinload(Transfer.dest_branch),
            selectinload(Transfer.origin_warehouse),
            selectinload(Transfer.dest_warehouse),
            selectinload(Transfer.lines)
        )
        .where(Transfer.company_id == current_user.company_id)
        .order_by(Transfer.request_date.desc())
    )

    if branch_id:
        try:
            b_uuid = uuid.UUID(branch_id)
            stmt = stmt.where((Transfer.origin_branch_id == b_uuid) | (Transfer.dest_branch_id == b_uuid))
        except ValueError:
            pass

    if warehouse_id:
        try:
            w_uuid = uuid.UUID(warehouse_id)
            stmt = stmt.where((Transfer.origin_warehouse_id == w_uuid) | (Transfer.dest_warehouse_id == w_uuid))
        except ValueError:
            pass

    res = await db.execute(stmt)
    transfers = res.scalars().all()

    return [
        {
            "id": str(t.id),
            "number": t.number,
            "origin_warehouse_name": t.origin_warehouse.name if t.origin_warehouse else (t.origin_branch.name if t.origin_branch else "Bodega Origen"),
            "dest_warehouse_name": t.dest_warehouse.name if t.dest_warehouse else (t.dest_branch.name if t.dest_branch else "Bodega Destino"),
            "origin_branch_name": t.origin_branch.name if t.origin_branch else "",
            "dest_branch_name": t.dest_branch.name if t.dest_branch else "",
            "state": t.state.value if hasattr(t.state, 'value') else str(t.state),
            "date": t.request_date.strftime("%d/%m/%Y") if t.request_date else "",
            "total_items": sum(float(l.qty_requested or 0) for l in t.lines),
            "notes": t.notes or "",
        }
        for t in transfers
    ]


@router.get("/{transfer_id}")
async def get_transfer(
    transfer_id: str,
    db: AsyncSession = Depends(get_db),
    current_user=Depends(get_current_user),
):
    """Obtener detalle de un traslado."""
    try:
        t_uuid = uuid.UUID(transfer_id)
    except ValueError:
        raise HTTPException(status_code=400, detail="ID de traslado inválido")

    stmt = (
        select(Transfer)
        .options(
            selectinload(Transfer.origin_branch),
            selectinload(Transfer.dest_branch),
            selectinload(Transfer.origin_warehouse),
            selectinload(Transfer.dest_warehouse),
            selectinload(Transfer.lines)
        )
        .where(Transfer.id == t_uuid, Transfer.company_id == current_user.company_id)
    )
    res = await db.execute(stmt)
    t = res.scalar_one_or_none()

    if not t:
        raise HTTPException(status_code=404, detail="Traslado no encontrado")

    return {
        "id": str(t.id),
        "number": t.number,
        "origin_branch_id": str(t.origin_branch_id),
        "origin_branch_name": t.origin_branch.name if t.origin_branch else "Sucursal Central",
        "origin_warehouse_id": str(t.origin_warehouse_id),
        "origin_warehouse_name": t.origin_warehouse.name if t.origin_warehouse else "Bodega Origen",
        "dest_branch_id": str(t.dest_branch_id),
        "dest_branch_name": t.dest_branch.name if t.dest_branch else "Sucursal Destino",
        "dest_warehouse_id": str(t.dest_warehouse_id),
        "dest_warehouse_name": t.dest_warehouse.name if t.dest_warehouse else "Bodega Destino",
        "state": t.state.value if hasattr(t.state, 'value') else str(t.state),
        "request_date": t.request_date.strftime("%d/%m/%Y %H:%M") if t.request_date else "",
        "send_date": t.send_date.strftime("%d/%m/%Y %H:%M") if t.send_date else None,
        "receive_date": t.receive_date.strftime("%d/%m/%Y %H:%M") if t.receive_date else None,
        "notes": t.notes or "",
        "rejection_reason": t.rejection_reason or "",
        "lines": [
            {
                "id": str(line.id),
                "product_id": str(line.product_id),
                "product_name": line.product_name,
                "qty_requested": float(line.qty_requested or 0),
                "qty_sent": float(line.qty_sent or 0),
                "qty_received": float(line.qty_received or 0),
                "qty_diff": float(line.qty_diff or 0),
                "unit_cost": float(line.unit_cost or 0),
                "notes": line.notes or "",
            }
            for line in t.lines
        ]
    }


@router.post("/")
async def create_transfer(
    data: CreateTransferIn,
    db: AsyncSession = Depends(get_db),
    current_user=Depends(get_current_user),
):
    """Solicitar y registrar un traslado entre bodegas."""
    try:
        ow_uuid = uuid.UUID(data.origin_warehouse_id)
        dw_uuid = uuid.UUID(data.dest_warehouse_id)
    except ValueError:
        raise HTTPException(status_code=400, detail="IDs de bodegas inválidos")

    if ow_uuid == dw_uuid:
        raise HTTPException(status_code=400, detail="La bodega de origen y destino no pueden ser la misma.")

    # Resolver sucursales de las bodegas
    ow_res = await db.execute(select(Warehouse).where(Warehouse.id == ow_uuid))
    ow = ow_res.scalar_one_or_none()
    dw_res = await db.execute(select(Warehouse).where(Warehouse.id == dw_uuid))
    dw = dw_res.scalar_one_or_none()

    if not ow or not dw:
        raise HTTPException(status_code=404, detail="Bodega de origen o destino no encontrada")

    ob_uuid = ow.branch_id
    db_uuid = dw.branch_id

    t_lines = [
        TransferLine(
            product_id=uuid.UUID(item.product_id),
            product_name=item.product_name,
            qty_requested=item.qty,
            qty_sent=Decimal("0"),
            qty_received=Decimal("0"),
            qty_diff=Decimal("0"),
            unit_cost=Decimal("0"),
        )
        for item in data.lines
    ]

    transfer = Transfer(
        company_id=current_user.company_id,
        origin_branch_id=ob_uuid,
        origin_warehouse_id=ow_uuid,
        dest_branch_id=db_uuid,
        dest_warehouse_id=dw_uuid,
        number=_generate_transfer_number(),
        state=TransferState.CONFIRMED,
        request_date=datetime.now(timezone.utc),
        requested_by=current_user.id,
        notes=data.notes,
        lines=t_lines,
    )

    db.add(transfer)
    await db.commit()
    await db.refresh(transfer)

    return {"id": str(transfer.id), "number": transfer.number, "status": "created"}


@router.post("/{transfer_id}/send")
async def send_transfer(
    transfer_id: str,
    data: Optional[SendTransferIn] = None,
    db: AsyncSession = Depends(get_db),
    current_user=Depends(get_current_user),
):
    """Despachar mercadería: validar stock en origen y registrar salida física y Kardex."""
    try:
        t_uuid = uuid.UUID(transfer_id)
    except ValueError:
        raise HTTPException(status_code=400, detail="ID de traslado inválido")

    stmt = (
        select(Transfer)
        .options(selectinload(Transfer.lines))
        .where(Transfer.id == t_uuid, Transfer.company_id == current_user.company_id)
    )
    res = await db.execute(stmt)
    t = res.scalar_one_or_none()

    if not t or t.state in [TransferState.SENT, TransferState.RECEIVED, TransferState.CANCELLED]:
        raise HTTPException(status_code=400, detail="El traslado no está disponible para envío o ya fue despachado.")

    inv_service = InventoryService(db)

    # Si se enviaron cantidades personalizadas de despacho
    sent_map = {}
    if data and data.lines:
        sent_map = {item.product_id: item.qty_sent for item in data.lines}

    # Validar existencias en origen antes de realizar cualquier movimiento
    for line in t.lines:
        pid_str = str(line.product_id)
        qty_to_send = sent_map.get(pid_str, line.qty_requested)
        if qty_to_send < 0:
            raise HTTPException(status_code=400, detail=f"La cantidad despachada no puede ser negativa para '{line.product_name}'.")

        # Consultar quant en origen
        quant_stmt = select(StockQuant).where(
            and_(
                StockQuant.product_id == line.product_id,
                StockQuant.warehouse_id == t.origin_warehouse_id,
                StockQuant.is_deleted == False
            )
        )
        quant_res = await db.execute(quant_stmt)
        quant = quant_res.scalar_one_or_none()
        stock_on_hand = quant.qty_on_hand if quant else Decimal("0")

        if stock_on_hand < qty_to_send:
            raise HTTPException(
                status_code=400,
                detail=f"Stock insuficiente en bodega origen para '{line.product_name}'. Disponible: {stock_on_hand}, a despachar: {qty_to_send}."
            )

    # Efectuar las salidas físicas
    for line in t.lines:
        pid_str = str(line.product_id)
        qty_to_send = sent_map.get(pid_str, line.qty_requested)
        line.qty_sent = qty_to_send

        # Obtener costo unitario promedio de bodega origen
        quant_stmt = select(StockQuant).where(
            and_(
                StockQuant.product_id == line.product_id,
                StockQuant.warehouse_id == t.origin_warehouse_id,
                StockQuant.is_deleted == False
            )
        )
        quant_res = await db.execute(quant_stmt)
        quant = quant_res.scalar_one_or_none()

        unit_cost = Decimal("0")
        if quant and quant.avg_cost and quant.avg_cost > 0:
            unit_cost = quant.avg_cost
        else:
            prod_res = await db.execute(select(Product).where(Product.id == line.product_id))
            prod = prod_res.scalar_one_or_none()
            if prod and prod.cost_price:
                unit_cost = prod.cost_price

        line.unit_cost = unit_cost

        # Descontar de bodega origen
        if line.qty_sent > 0:
            await inv_service.create_move(
                warehouse_id=t.origin_warehouse_id,
                product_id=line.product_id,
                move_type=StockMoveType.TRANSFER_OUT,
                qty=line.qty_sent,
                unit_cost=unit_cost,
                reference=f"Envío Traslado {t.number}",
                reference_id=t.id,
                user_id=current_user.id,
            )

    t.state = TransferState.SENT
    t.send_date = datetime.now(timezone.utc)
    t.sent_by = current_user.id
    if data and data.notes:
        t.notes = (t.notes + "\n" if t.notes else "") + f"[Despacho] {data.notes}"

    await db.commit()
    return {"id": str(t.id), "number": t.number, "state": t.state.value, "status": "sent"}


@router.post("/{transfer_id}/receive")
async def receive_transfer(
    transfer_id: str,
    data: Optional[ReceiveTransferIn] = None,
    db: AsyncSession = Depends(get_db),
    current_user=Depends(get_current_user),
):
    """Confirmar recepción de mercadería: conteo físico, discrepancias e ingreso a Kardex."""
    try:
        t_uuid = uuid.UUID(transfer_id)
    except ValueError:
        raise HTTPException(status_code=400, detail="ID de traslado inválido")

    stmt = (
        select(Transfer)
        .options(selectinload(Transfer.lines))
        .where(Transfer.id == t_uuid, Transfer.company_id == current_user.company_id)
    )
    res = await db.execute(stmt)
    t = res.scalar_one_or_none()

    if not t or t.state in [TransferState.RECEIVED, TransferState.CANCELLED]:
        raise HTTPException(status_code=400, detail="El traslado no está disponible para recepción.")

    inv_service = InventoryService(db)

    # Si aún no se había despachado formalmente, despachar primero origen
    if t.state not in [TransferState.SENT, TransferState.IN_TRANSIT]:
        for line in t.lines:
            line.qty_sent = line.qty_requested
            quant_res = await db.execute(
                select(StockQuant).where(
                    and_(
                        StockQuant.product_id == line.product_id,
                        StockQuant.warehouse_id == t.origin_warehouse_id,
                        StockQuant.is_deleted == False
                    )
                )
            )
            quant = quant_res.scalar_one_or_none()
            available_stock = quant.qty_on_hand if quant else Decimal("0")
            if available_stock < line.qty_sent:
                raise HTTPException(
                    status_code=400,
                    detail=f"Stock insuficiente en bodega origen para despachar '{line.product_name}'. Disponible: {float(available_stock):.0f}, Requerido: {float(line.qty_sent):.0f}."
                )
            cost = quant.avg_cost if (quant and quant.avg_cost and quant.avg_cost > 0) else Decimal("0")
            line.unit_cost = cost
            if line.qty_sent > 0:
                await inv_service.create_move(
                    warehouse_id=t.origin_warehouse_id,
                    product_id=line.product_id,
                    move_type=StockMoveType.TRANSFER_OUT,
                    qty=line.qty_sent,
                    unit_cost=cost,
                    reference=f"Salida Traslado {t.number}",
                    reference_id=t.id,
                    user_id=current_user.id,
                )
        t.send_date = datetime.now(timezone.utc)
        t.sent_by = current_user.id

    recv_map = {}
    notes_map = {}
    is_final = True
    if data:
        is_final = data.is_final
        if data.lines:
            for item in data.lines:
                recv_map[item.product_id] = item.qty_received
                if item.notes:
                    notes_map[item.product_id] = item.notes

    has_differences = False
    for line in t.lines:
        pid_str = str(line.product_id)
        qty_rec = recv_map.get(pid_str, line.qty_sent or line.qty_requested)
        if qty_rec < 0:
            raise HTTPException(status_code=400, detail=f"La cantidad recibida no puede ser negativa para '{line.product_name}'.")

        line.qty_received = qty_rec
        expected_sent = line.qty_sent or line.qty_requested
        line.qty_diff = expected_sent - line.qty_received
        if line.qty_diff != 0:
            has_differences = True

        if pid_str in notes_map:
            line.notes = notes_map[pid_str]

        # Ingresar a bodega destino únicamente la cantidad física recibida
        if line.qty_received > 0:
            await inv_service.create_move(
                warehouse_id=t.dest_warehouse_id,
                product_id=line.product_id,
                move_type=StockMoveType.TRANSFER_IN,
                qty=line.qty_received,
                unit_cost=line.unit_cost or Decimal("0"),
                reference=f"Recepción Traslado {t.number}",
                reference_id=t.id,
                user_id=current_user.id,
            )

    if has_differences and not is_final:
        t.state = TransferState.PARTIAL
    else:
        t.state = TransferState.RECEIVED

    t.receive_date = datetime.now(timezone.utc)
    t.received_by = current_user.id
    if data and data.notes:
        t.notes = (t.notes + "\n" if t.notes else "") + f"[Recepción] {data.notes}"

    await db.commit()
    return {"id": str(t.id), "number": t.number, "state": t.state.value, "status": t.state.value}


@router.post("/{transfer_id}/cancel")
async def cancel_transfer(
    transfer_id: str,
    data: Optional[CancelTransferIn] = None,
    db: AsyncSession = Depends(get_db),
    current_user=Depends(get_current_user),
):
    """Cancelar una solicitud de traslado pendiente antes de su despacho."""
    try:
        t_uuid = uuid.UUID(transfer_id)
    except ValueError:
        raise HTTPException(status_code=400, detail="ID de traslado inválido")

    stmt = select(Transfer).where(Transfer.id == t_uuid, Transfer.company_id == current_user.company_id)
    res = await db.execute(stmt)
    t = res.scalar_one_or_none()

    if not t:
        raise HTTPException(status_code=404, detail="Traslado no encontrado")

    if t.state in [TransferState.SENT, TransferState.RECEIVED, TransferState.CANCELLED]:
        raise HTTPException(status_code=400, detail=f"No se puede cancelar un traslado en estado '{t.state.value}'.")

    t.state = TransferState.CANCELLED
    if data and data.reason:
        t.rejection_reason = data.reason
        t.notes = (t.notes + "\n" if t.notes else "") + f"[Cancelado] {data.reason}"

    await db.commit()
    return {"id": str(t.id), "number": t.number, "state": t.state.value, "status": "cancelled"}
