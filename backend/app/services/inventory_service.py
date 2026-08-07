"""Servicio de Inventario: movimientos, Kardex, stock y alertas."""
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy import select, update, func, and_
from sqlalchemy.orm import selectinload
from decimal import Decimal
from typing import Optional
from uuid import UUID
import logging

from app.models.inventory import StockMove, StockLot, StockQuant, StockMoveType
from app.models.product import Product

logger = logging.getLogger(__name__)


class InventoryService:
    def __init__(self, db: AsyncSession):
        self.db = db

    async def get_stock(self, product_id: UUID, warehouse_id: UUID, variant_id: Optional[UUID] = None) -> Decimal:
        """Obtener stock disponible de un producto en una bodega."""
        stmt = select(StockQuant).where(
            and_(
                StockQuant.product_id == product_id,
                StockQuant.warehouse_id == warehouse_id,
                StockQuant.variant_id == variant_id,
                StockQuant.is_deleted == False,
            )
        )
        result = await self.db.execute(stmt)
        quant = result.scalar_one_or_none()
        if not quant:
            return Decimal("0")
        return quant.qty_on_hand - quant.qty_reserved

    async def create_move(
        self,
        warehouse_id: UUID,
        product_id: UUID,
        move_type: StockMoveType,
        qty: Decimal,
        unit_cost: Decimal = Decimal("0"),
        reference: str = "",
        reference_id: Optional[UUID] = None,
        lot_id: Optional[UUID] = None,
        variant_id: Optional[UUID] = None,
        user_id: Optional[UUID] = None,
        notes: str = "",
    ) -> StockMove:
        """Crear movimiento de inventario y actualizar stock."""
        # Obtener o crear el quant
        quant = await self._get_or_create_quant(product_id, warehouse_id, variant_id)

        qty_before = quant.qty_on_hand

        # Determinar si es entrada o salida
        is_out = move_type in [
            StockMoveType.SALE, StockMoveType.TRANSFER_OUT,
            StockMoveType.ADJUSTMENT_OUT, StockMoveType.WASTE, StockMoveType.EXPIRED
        ]

        if is_out:
            if qty_before < qty:
                logger.warning(
                    f"Stock insuficiente para producto {product_id}: "
                    f"disponible={qty_before}, solicitado={qty}"
                )
            quant.qty_on_hand -= qty
        else:
            quant.qty_on_hand += qty

        qty_after = quant.qty_on_hand

        # Actualizar costo promedio (solo en entradas)
        if not is_out and unit_cost > 0:
            await self._update_avg_cost(quant, qty, unit_cost, qty_before)

        # Crear el movimiento (Kardex)
        move = StockMove(
            warehouse_id=warehouse_id,
            product_id=product_id,
            lot_id=lot_id,
            variant_id=variant_id,
            move_type=move_type,
            reference=reference,
            reference_id=reference_id,
            qty=qty,
            qty_before=qty_before,
            qty_after=qty_after,
            unit_cost=unit_cost,
            total_cost=qty * unit_cost,
            notes=notes,
            user_id=user_id,
        )

        self.db.add(move)
        await self.db.flush()

        # Verificar alertas de stock mínimo
        await self._check_stock_alert(product_id, warehouse_id, qty_after)

        return move

    async def _get_or_create_quant(
        self,
        product_id: UUID,
        warehouse_id: UUID,
        variant_id: Optional[UUID],
    ) -> StockQuant:
        stmt = select(StockQuant).where(
            and_(
                StockQuant.product_id == product_id,
                StockQuant.warehouse_id == warehouse_id,
                StockQuant.variant_id == variant_id,
                StockQuant.is_deleted == False,
            )
        )
        result = await self.db.execute(stmt)
        quant = result.scalar_one_or_none()

        if not quant:
            quant = StockQuant(
                product_id=product_id,
                warehouse_id=warehouse_id,
                variant_id=variant_id,
                qty_on_hand=Decimal("0"),
                qty_reserved=Decimal("0"),
                qty_incoming=Decimal("0"),
                avg_cost=Decimal("0"),
            )
            self.db.add(quant)
            await self.db.flush()

        return quant

    async def _update_avg_cost(
        self,
        quant: StockQuant,
        incoming_qty: Decimal,
        incoming_cost: Decimal,
        qty_before: Decimal,
    ):
        """Actualizar costo promedio ponderado."""
        existing_value = qty_before * quant.avg_cost
        new_value = incoming_qty * incoming_cost
        new_total_qty = qty_before + incoming_qty
        if new_total_qty > 0:
            quant.avg_cost = (existing_value + new_value) / new_total_qty

    async def _check_stock_alert(self, product_id: UUID, warehouse_id: UUID, current_qty: Decimal):
        """Verificar si se alcanzó el stock mínimo y emitir alerta."""
        result = await self.db.execute(
            select(Product).where(Product.id == product_id)
        )
        product = result.scalar_one_or_none()
        if product and product.min_stock > 0 and current_qty <= product.min_stock:
            logger.warning(
                f"⚠️ ALERTA STOCK MÍNIMO: {product.name} "
                f"bodega={warehouse_id}, stock={current_qty}, mínimo={product.min_stock}"
            )
            # TODO: Publicar evento Redis para notificación WebSocket
            # await publish_event(f"stock_alert:{warehouse_id}", {...})

    async def get_kardex(
        self,
        product_id: UUID,
        warehouse_id: Optional[UUID] = None,
        limit: int = 100,
        offset: int = 0,
    ) -> list[StockMove]:
        """Obtener kardex de movimientos de un producto."""
        stmt = (
            select(StockMove)
            .where(
                and_(
                    StockMove.product_id == product_id,
                    StockMove.is_deleted == False,
                    *(([StockMove.warehouse_id == warehouse_id]) if warehouse_id else [])
                )
            )
            .order_by(StockMove.created_at.desc())
            .offset(offset)
            .limit(limit)
        )
        result = await self.db.execute(stmt)
        return result.scalars().all()

    async def get_low_stock_products(
        self, company_id: UUID, warehouse_id: Optional[UUID] = None
    ) -> list[dict]:
        """Productos con stock igual o menor al mínimo."""
        stmt = (
            select(
                Product.id,
                Product.name,
                Product.internal_ref,
                Product.barcode,
                Product.min_stock,
                StockQuant.qty_on_hand,
                StockQuant.warehouse_id,
            )
            .join(StockQuant, StockQuant.product_id == Product.id)
            .where(
                and_(
                    Product.company_id == company_id,
                    Product.is_active == True,
                    Product.min_stock > 0,
                    StockQuant.qty_on_hand <= Product.min_stock,
                    *(([StockQuant.warehouse_id == warehouse_id]) if warehouse_id else []),
                )
            )
            .order_by(StockQuant.qty_on_hand.asc())
        )
        result = await self.db.execute(stmt)
        rows = result.fetchall()
        return [
            {
                "product_id": str(r.id),
                "name": r.name,
                "internal_ref": r.internal_ref,
                "barcode": r.barcode,
                "min_stock": float(r.min_stock),
                "qty_on_hand": float(r.qty_on_hand),
                "shortage": float(r.min_stock - r.qty_on_hand),
            }
            for r in rows
        ]
