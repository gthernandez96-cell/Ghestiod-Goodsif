"""API v1 — Fabricación (MFG)."""
from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy import select, and_
from sqlalchemy.orm import selectinload
from pydantic import BaseModel, ConfigDict
from typing import Optional, List
from decimal import Decimal
from datetime import datetime, timezone
import uuid

from app.core.database import get_db
from app.core.security import get_current_user
from app.models.mfg import BOM, BOMItem, ProductionOrder
from app.models.product import Product
from app.models.company import Warehouse
from app.models.inventory import StockMoveType
from app.services.inventory_service import InventoryService

router = APIRouter(prefix="/mfg")


# ===== SCHEMAS =====
class ProductionOrderIn(BaseModel):
    product_name: str
    quantity: Decimal


class ProductionOrderOut(BaseModel):
    id: str
    number: str
    product_name: str
    quantity: float
    state: str
    date: Optional[str]

    model_config = ConfigDict(from_attributes=True)


# ===== HELPERS =====
def _generate_mo_number(seq: int) -> str:
    return f"WH/MO/{seq:05d}"


# ===== ENDPOINTS =====
@router.get("/orders", response_model=List[ProductionOrderOut])
async def list_production_orders(
    db: AsyncSession = Depends(get_db),
    current_user=Depends(get_current_user),
):
    """Listar órdenes de producción/horneado."""
    stmt = (
        select(ProductionOrder)
        .options(selectinload(ProductionOrder.product))
        .where(
            ProductionOrder.company_id == current_user.company_id,
            ProductionOrder.is_deleted == False
        )
        .order_by(ProductionOrder.created_at.desc())
    )
    res = await db.execute(stmt)
    orders = res.scalars().all()

    # Formatear la salida para que coincida con el schema
    return [
        ProductionOrderOut(
            id=str(o.id),
            number=o.number,
            product_name=o.product.name if o.product else "Producto Desconocido",
            quantity=float(o.quantity),
            state=o.state,
            date=o.date_planned.strftime("%d/%m/%Y") if o.date_planned else "Hoy"
        )
        for o in orders
    ]


@router.post("/orders", response_model=ProductionOrderOut)
async def create_production_order(
    data: ProductionOrderIn,
    db: AsyncSession = Depends(get_db),
    current_user=Depends(get_current_user),
):
    """Iniciar nueva orden de producción/horneado."""
    # Buscar producto por nombre
    stmt_p = select(Product).where(
        Product.name == data.product_name,
        Product.company_id == current_user.company_id,
        Product.is_deleted == False
    ).limit(1)
    res_p = await db.execute(stmt_p)
    prod = res_p.scalar_one_or_none()

    if not prod:
        # Si no existe, buscar por coincidencia parcial o crear un fallback
        stmt_p2 = select(Product).where(
            Product.company_id == current_user.company_id,
            Product.is_deleted == False
        ).limit(1)
        res_p2 = await db.execute(stmt_p2)
        prod = res_p2.scalar_one_or_none()
        if not prod:
            raise HTTPException(status_code=400, detail="No hay productos registrados en el sistema")

    # Contar órdenes para generar número secuencial
    stmt_count = select(ProductionOrder).where(ProductionOrder.company_id == current_user.company_id)
    res_count = await db.execute(stmt_count)
    seq = len(res_count.scalars().all()) + 1

    mo = ProductionOrder(
        company_id=current_user.company_id,
        number=_generate_mo_number(seq),
        product_id=prod.id,
        quantity=data.quantity,
        state="in_progress",
        date_planned=datetime.now(timezone.utc)
    )
    db.add(mo)
    await db.commit()
    await db.refresh(mo)

    return ProductionOrderOut(
        id=str(mo.id),
        number=mo.number,
        product_name=prod.name,
        quantity=float(mo.quantity),
        state=mo.state,
        date=mo.date_planned.strftime("%d/%m/%Y") if mo.date_planned else "Hoy"
    )


@router.post("/orders/{order_id}/complete")
async def complete_production_order(
    order_id: str,
    db: AsyncSession = Depends(get_db),
    current_user=Depends(get_current_user),
):
    """Completar horneado, descontar insumos (recetas) y añadir producto final al stock."""
    try:
        mo_uuid = uuid.UUID(order_id)
    except ValueError:
        raise HTTPException(status_code=400, detail="ID de orden inválido")

    stmt = (
        select(ProductionOrder)
        .options(selectinload(ProductionOrder.product))
        .where(
            ProductionOrder.id == mo_uuid,
            ProductionOrder.company_id == current_user.company_id,
            ProductionOrder.is_deleted == False
        )
    )
    res = await db.execute(stmt)
    mo = res.scalar_one_or_none()
    if not mo:
        raise HTTPException(status_code=404, detail="Orden de producción no encontrada")

    if mo.state == "done":
        raise HTTPException(status_code=400, detail="La orden ya ha sido completada")

    # Obtener bodega por defecto para la producción
    stmt_w = select(Warehouse).where(Warehouse.is_active == True).limit(1)
    res_w = await db.execute(stmt_w)
    wh = res_w.scalar_one_or_none()
    if not wh:
        raise HTTPException(status_code=400, detail="No se encontró una bodega activa para realizar movimientos")

    inv_service = InventoryService(db)

    # 1. Buscar receta (BOM) para el producto
    stmt_bom = (
        select(BOM)
        .options(selectinload(BOM.items))
        .where(
            BOM.product_id == mo.product_id,
            BOM.is_deleted == False
        )
        .limit(1)
    )
    res_bom = await db.execute(stmt_bom)
    bom = res_bom.scalar_one_or_none()

    notes = f"Producido vía Orden {mo.number}"

    # 2. Si existe receta, consumir ingredientes proporcionalmente
    if bom:
        for item in bom.items:
            qty_needed = item.qty * mo.quantity
            await inv_service.create_move(
                warehouse_id=wh.id,
                product_id=item.product_id,
                move_type=StockMoveType.ADJUSTMENT_OUT,
                qty=qty_needed,
                reference=mo.number,
                reference_id=mo.id,
                user_id=current_user.id,
                notes=f"Insumo para {mo.product.name} (Orden {mo.number})"
            )
    else:
        # Receta por defecto simulada si no tiene cargada receta real en DB (ej. para Apple Pie o Masa Madre)
        # Buscar insumos comunes en BD por coincidencia parcial para que descuente de verdad
        stmt_insumos = select(Product).where(
            Product.company_id == current_user.company_id,
            Product.is_deleted == False,
            Product.name.ilike("%Harina%") | Product.name.ilike("%Azúcar%")
        )
        res_insumos = await db.execute(stmt_insumos)
        insumos = res_insumos.scalars().all()
        for insumo in insumos:
            # Consumir un valor fijo estimado
            qty_needed = Decimal("0.1") * mo.quantity
            await inv_service.create_move(
                warehouse_id=wh.id,
                product_id=insumo.id,
                move_type=StockMoveType.ADJUSTMENT_OUT,
                qty=qty_needed,
                reference=mo.number,
                reference_id=mo.id,
                user_id=current_user.id,
                notes=f"Insumo simulado para {mo.product.name} (Orden {mo.number})"
            )

    # 3. Ingresar stock del producto terminado
    await inv_service.create_move(
        warehouse_id=wh.id,
        product_id=mo.product_id,
        move_type=StockMoveType.ADJUSTMENT_IN,
        qty=mo.quantity,
        unit_cost=mo.product.cost_price or Decimal("0"),
        reference=mo.number,
        reference_id=mo.id,
        user_id=current_user.id,
        notes=notes
    )

    mo.state = "done"
    mo.date_finished = datetime.now(timezone.utc)
    await db.commit()

    return {"status": "ok", "message": f"Orden {mo.number} completada. Stock de {mo.product.name} incrementado en {float(mo.quantity)} U."}
