"""API v1 — Inventario y Kardex."""
from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy import select, or_, func
from sqlalchemy.orm import selectinload
from pydantic import BaseModel
from typing import Optional, List
from decimal import Decimal
import uuid

from app.core.database import get_db
from app.core.security import get_current_user
from app.models.inventory import StockQuant, StockMove, StockMoveType
from app.models.product import Product
from app.models.company import Warehouse
from app.services.inventory_service import InventoryService

router = APIRouter(prefix="/inventory")


class StockAdjustIn(BaseModel):
    warehouse_id: str
    product_id: str
    qty: Decimal
    adjust_type: str = "in"  # "in" or "out"
    reference: Optional[str] = "Ajuste manual"
    notes: Optional[str] = None


@router.get("/stock")
async def list_stock(
    warehouse_id: Optional[str] = Query(None),
    product_id: Optional[str] = Query(None),
    db: AsyncSession = Depends(get_db),
    current_user=Depends(get_current_user),
):
    """Listar existencias por producto y bodega."""
    stmt = (
        select(StockQuant)
        .options(selectinload(StockQuant.product), selectinload(StockQuant.warehouse))
        .where(StockQuant.is_deleted == False)
    )

    if warehouse_id:
        try:
            stmt = stmt.where(StockQuant.warehouse_id == uuid.UUID(warehouse_id))
        except ValueError:
            pass

    if product_id:
        try:
            stmt = stmt.where(StockQuant.product_id == uuid.UUID(product_id))
        except ValueError:
            pass

    res = await db.execute(stmt)
    quants = res.scalars().all()

    return [
        {
            "id": str(q.id),
            "product_id": str(q.product_id),
            "product_name": q.product.name if q.product else "N/A",
            "product_ref": q.product.internal_ref if q.product else "",
            "warehouse_id": str(q.warehouse_id),
            "warehouse_name": q.warehouse.name if q.warehouse else "N/A",
            "qty_on_hand": float(q.qty_on_hand or 0),
            "qty_reserved": float(q.qty_reserved or 0),
            "qty_available": float((q.qty_on_hand or 0) - (q.qty_reserved or 0)),
            "avg_cost": float(q.avg_cost or 0),
        }
        for q in quants
    ]


@router.get("/moves")
async def list_moves(
    warehouse_id: Optional[str] = Query(None),
    product_id: Optional[str] = Query(None),
    limit: int = Query(50, le=200),
    db: AsyncSession = Depends(get_db),
    current_user=Depends(get_current_user),
):
    """Kardex: Listar movimientos de inventario."""
    stmt = (
        select(StockMove)
        .options(selectinload(StockMove.product), selectinload(StockMove.warehouse))
        .order_by(StockMove.created_at.desc())
        .limit(limit)
    )

    if warehouse_id:
        try:
            stmt = stmt.where(StockMove.warehouse_id == uuid.UUID(warehouse_id))
        except ValueError:
            pass

    if product_id:
        try:
            stmt = stmt.where(StockMove.product_id == uuid.UUID(product_id))
        except ValueError:
            pass

    res = await db.execute(stmt)
    moves = res.scalars().all()

    return [
        {
            "id": str(m.id),
            "date": m.created_at.isoformat() if m.created_at else None,
            "product_name": m.product.name if m.product else "N/A",
            "warehouse_name": m.warehouse.name if m.warehouse else "N/A",
            "move_type": m.move_type.value if hasattr(m.move_type, 'value') else str(m.move_type),
            "reference": m.reference or "",
            "qty": float(m.qty or 0),
            "qty_before": float(m.qty_before or 0),
            "qty_after": float(m.qty_after or 0),
            "unit_cost": float(m.unit_cost or 0),
            "total_cost": float(m.total_cost or 0),
            "notes": m.notes or "",
        }
        for m in moves
    ]


@router.post("/adjust")
async def adjust_stock(
    data: StockAdjustIn,
    db: AsyncSession = Depends(get_db),
    current_user=Depends(get_current_user),
):
    """Realizar un ajuste manual de stock (Entrada o Salida)."""
    try:
        w_uuid = uuid.UUID(data.warehouse_id)
        p_uuid = uuid.UUID(data.product_id)
    except ValueError:
        raise HTTPException(status_code=400, detail="IDs de bodega o producto inválidos")

    inv_service = InventoryService(db)
    m_type = StockMoveType.ADJUSTMENT_IN if data.adjust_type.lower() == "in" else StockMoveType.ADJUSTMENT_OUT

    move = await inv_service.create_move(
        warehouse_id=w_uuid,
        product_id=p_uuid,
        move_type=m_type,
        qty=data.qty,
        reference=data.reference or "Ajuste manual",
        user_id=current_user.id,
        notes=data.notes or "",
    )
    await db.commit()

    return {
        "move_id": str(move.id),
        "status": "ok",
        "qty_before": float(move.qty_before),
        "qty_after": float(move.qty_after),
    }


@router.get("/low-stock")
async def low_stock_alerts(
    db: AsyncSession = Depends(get_db),
    current_user=Depends(get_current_user),
):
    """Listar productos cuyo stock global está por debajo del stock mínimo."""
    # Obtener productos con min_stock > 0
    prod_stmt = select(Product).where(
        Product.company_id == current_user.company_id,
        Product.is_active == True,
        Product.min_stock > 0
    )
    prod_res = await db.execute(prod_stmt)
    products = prod_res.scalars().all()

    alerts = []
    for p in products:
        q_stmt = select(func.sum(StockQuant.qty_on_hand)).where(
            StockQuant.product_id == p.id,
            StockQuant.is_deleted == False
        )
        q_res = await db.execute(q_stmt)
        total_qty = float(q_res.scalar() or 0)
        min_stock = float(p.min_stock or 0)

        if total_qty <= min_stock:
            alerts.append({
                "product_id": str(p.id),
                "name": p.name,
                "internal_ref": p.internal_ref or "",
                "current_stock": total_qty,
                "min_stock": min_stock,
                "deficit": min_stock - total_qty,
            })

    return alerts


# ===== AGREGADOS PARA Abarrotería/Panadería =====
class WasteIn(BaseModel):
    product_id: str
    qty: Decimal
    reason: str = "Merma diaria"


@router.post("/waste")
async def register_waste(
    data: WasteIn,
    db: AsyncSession = Depends(get_db),
    current_user=Depends(get_current_user),
):
    """Registrar merma o desperdicio del día (descuenta stock)."""
    # Obtener bodega por defecto
    from app.models.company import Warehouse
    res_w = await db.execute(select(Warehouse.id).limit(1))
    wh_id = res_w.scalar_one_or_none()
    if not wh_id:
        raise HTTPException(status_code=400, detail="No hay bodegas configuradas")

    try:
        prod_uuid = uuid.UUID(data.product_id)
    except ValueError:
        raise HTTPException(status_code=400, detail="ID de producto inválido")

    # Registrar el movimiento
    from app.services.inventory_service import InventoryService
    from app.models.inventory import StockMoveType
    inv_service = InventoryService(db)
    
    move = await inv_service.create_move(
        warehouse_id=wh_id,
        product_id=prod_uuid,
        move_type=StockMoveType.WASTE,
        qty=data.qty,
        user_id=current_user.id,
        notes=data.reason
    )
    await db.commit()
    return {"status": "ok", "message": f"Merma de {float(data.qty)} U registrada con éxito."}


@router.get("/expirations")
async def list_expiring_lots(
    db: AsyncSession = Depends(get_db),
    current_user=Depends(get_current_user),
):
    """Listar lotes próximos a vencer (abarrotería/farmacia)."""
    from app.models.inventory import StockLot
    from datetime import datetime, timezone, timedelta
    limit_date = datetime.now(timezone.utc) + timedelta(days=30)

    stmt = (
        select(StockLot)
        .options(selectinload(StockLot.product))
        .where(
            StockLot.expiry_date != None,
            StockLot.expiry_date <= limit_date,
            StockLot.qty_on_hand > 0,
            StockLot.is_deleted == False
        )
        .order_by(StockLot.expiry_date.asc())
    )
    res = await db.execute(stmt)
    lots = res.scalars().all()

    return [
        {
            "id": str(lot.id),
            "product_name": lot.product.name if lot.product else "Desconocido",
            "lot_number": lot.lot_number,
            "expiry_date": lot.expiry_date.strftime("%d/%m/%Y") if lot.expiry_date else "",
            "days_left": (lot.expiry_date.replace(tzinfo=timezone.utc) - datetime.now(timezone.utc)).days,
            "qty_on_hand": float(lot.qty_on_hand)
        }
        for lot in lots
    ]


@router.get("/reorder-rules")
async def list_reorder_rules(
    db: AsyncSession = Depends(get_db),
    current_user=Depends(get_current_user),
):
    """Reglas de Reordenamiento Automático Odoo (Min/Max Stock)."""
    stmt = select(Product).where(Product.is_deleted == False, Product.is_active == True)
    res = await db.execute(stmt)
    products = res.scalars().all()

    rules = []
    for p in products:
        stock = float(p.stock or 0)
        min_qty = float(p.min_stock_qty or 5)
        max_qty = float(p.max_stock_qty or 20)

        if stock <= min_qty:
            suggested_order = max_qty - stock
            rules.append({
                "product_id": str(p.id),
                "product_name": p.name,
                "sku": p.sku or "N/A",
                "current_stock": stock,
                "min_stock": min_qty,
                "max_stock": max_qty,
                "suggested_order_qty": round(suggested_order, 2),
                "status": "CRITICAL" if stock <= 0 else "WARNING",
                "action": "Generar Borrador de Compra"
            })

    return rules


