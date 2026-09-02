"""API v1 — Traslados Internos entre Bodegas 360°."""
from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy import select, func
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
        elif state_str == "received":
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
        "lines": [
            {
                "id": str(line.id),
                "product_id": str(line.product_id),
                "product_name": line.product_name,
                "qty_requested": float(line.qty_requested or 0),
                "qty_sent": float(line.qty_sent or 0),
                "qty_received": float(line.qty_received or 0),
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
    db: AsyncSession = Depends(get_db),
    current_user=Depends(get_current_user),
):
    """Despachar mercadería: registrar salida física de bodega origen."""
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
        raise HTTPException(status_code=400, detail="Traslado no disponible para envío")

    inv_service = InventoryService(db)

    for line in t.lines:
        line.qty_sent = line.qty_requested
        # Descontar de bodega origen
        await inv_service.create_move(
            warehouse_id=t.origin_warehouse_id,
            product_id=line.product_id,
            move_type=StockMoveType.TRANSFER_OUT,
            qty=line.qty_sent,
            reference=f"Envío Traslado {t.number}",
            reference_id=t.id,
            user_id=current_user.id,
        )

    t.state = TransferState.SENT
    t.send_date = datetime.now(timezone.utc)
    t.sent_by = current_user.id

    await db.commit()
    return {"id": str(t.id), "number": t.number, "state": t.state.value, "status": "sent"}


@router.post("/{transfer_id}/receive")
async def receive_transfer(
    transfer_id: str,
    db: AsyncSession = Depends(get_db),
    current_user=Depends(get_current_user),
):
    """Confirmar recepción de mercadería: registrar ingreso físico a bodega destino."""
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

    if not t or t.state == TransferState.RECEIVED:
        raise HTTPException(status_code=400, detail="Traslado no disponible para recepción")

    inv_service = InventoryService(db)

    # Si aún no se había despachado formalmente, descontar origen primero
    if t.state != TransferState.SENT:
        for line in t.lines:
            line.qty_sent = line.qty_requested
            await inv_service.create_move(
                warehouse_id=t.origin_warehouse_id,
                product_id=line.product_id,
                move_type=StockMoveType.TRANSFER_OUT,
                qty=line.qty_sent,
                reference=f"Salida Traslado {t.number}",
                reference_id=t.id,
                user_id=current_user.id,
            )

    for line in t.lines:
        line.qty_received = line.qty_sent or line.qty_requested
        # Ingresar a bodega destino
        await inv_service.create_move(
            warehouse_id=t.dest_warehouse_id,
            product_id=line.product_id,
            move_type=StockMoveType.TRANSFER_IN,
            qty=line.qty_received,
            reference=f"Recepción Traslado {t.number}",
            reference_id=t.id,
            user_id=current_user.id,
        )

    t.state = TransferState.RECEIVED
    t.receive_date = datetime.now(timezone.utc)
    t.received_by = current_user.id

    await db.commit()
    return {"id": str(t.id), "number": t.number, "state": t.state.value, "status": "received"}
