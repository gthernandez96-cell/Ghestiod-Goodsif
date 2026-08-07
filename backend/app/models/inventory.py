"""Modelos: Inventario, Movimientos de Stock, Lotes."""
from sqlalchemy import (
    Column, String, Boolean, Integer, ForeignKey, Text, Numeric,
    Enum as SAEnum, DateTime, Index
)
from sqlalchemy.dialects.postgresql import UUID, JSONB
from sqlalchemy.orm import relationship
from sqlalchemy import func
import enum
from app.models.base import BaseModel


class StockMoveType(str, enum.Enum):
    # Entradas
    PURCHASE = "purchase"           # Compra
    TRANSFER_IN = "transfer_in"    # Traslado recibido
    ADJUSTMENT_IN = "adj_in"       # Ajuste positivo
    RETURN_PURCHASE = "ret_purch"  # Devolución a proveedor (entrada)
    INITIAL = "initial"            # Inventario inicial

    # Salidas
    SALE = "sale"                  # Venta
    TRANSFER_OUT = "transfer_out"  # Traslado enviado
    ADJUSTMENT_OUT = "adj_out"     # Ajuste negativo
    RETURN_SALE = "ret_sale"       # Devolución de cliente
    WASTE = "waste"                # Merma/desperdicio
    EXPIRED = "expired"            # Vencido


class StockMove(BaseModel):
    """Movimiento de stock (Kardex)."""
    __tablename__ = "stock_moves"

    warehouse_id = Column(UUID(as_uuid=True), ForeignKey("warehouses.id"), nullable=False)
    product_id = Column(UUID(as_uuid=True), ForeignKey("products.id"), nullable=False)
    lot_id = Column(UUID(as_uuid=True), ForeignKey("stock_lots.id"), nullable=True)
    variant_id = Column(UUID(as_uuid=True), ForeignKey("product_variants.id"), nullable=True)

    move_type = Column(SAEnum(StockMoveType), nullable=False)
    reference = Column(String(200))  # Número de documento origen
    reference_id = Column(UUID(as_uuid=True))  # ID del documento origen

    # Cantidades
    qty = Column(Numeric(18, 4), nullable=False)
    qty_before = Column(Numeric(18, 4), default=0)   # Stock antes del movimiento
    qty_after = Column(Numeric(18, 4), default=0)    # Stock después del movimiento

    # Costos
    unit_cost = Column(Numeric(18, 4), default=0)
    total_cost = Column(Numeric(18, 4), default=0)

    # Metadata
    notes = Column(Text)
    user_id = Column(UUID(as_uuid=True), ForeignKey("users.id"))

    # Relationships
    warehouse = relationship("Warehouse", back_populates="stock_moves")
    product = relationship("Product")
    lot = relationship("StockLot")

    __table_args__ = (
        Index("ix_stock_moves_product_warehouse", "product_id", "warehouse_id"),
        Index("ix_stock_moves_reference", "reference"),
        Index("ix_stock_moves_created_at", "created_at"),
    )


class StockLot(BaseModel):
    """Lote de producto (farmacia: vencimiento; ferretería: número de serie)."""
    __tablename__ = "stock_lots"

    product_id = Column(UUID(as_uuid=True), ForeignKey("products.id"), nullable=False)
    warehouse_id = Column(UUID(as_uuid=True), ForeignKey("warehouses.id"), nullable=False)
    lot_number = Column(String(200), nullable=False)  # Número de lote o serie
    serial_number = Column(String(200))               # Para tracking serial
    expiry_date = Column(DateTime(timezone=True))     # Fecha de vencimiento
    manufacture_date = Column(DateTime(timezone=True))
    qty_on_hand = Column(Numeric(18, 4), default=0)
    unit_cost = Column(Numeric(18, 4), default=0)
    is_active = Column(Boolean, default=True)

    # Relationships
    product = relationship("Product", back_populates="stock_lots")
    warehouse = relationship("Warehouse", back_populates="stock_lots")

    __table_args__ = (
        Index("ix_stock_lots_lot_number", "lot_number"),
        Index("ix_stock_lots_expiry", "expiry_date"),
    )


class StockQuant(BaseModel):
    """Stock disponible por producto/bodega (tabla de resumen)."""
    __tablename__ = "stock_quants"

    product_id = Column(UUID(as_uuid=True), ForeignKey("products.id"), nullable=False)
    warehouse_id = Column(UUID(as_uuid=True), ForeignKey("warehouses.id"), nullable=False)
    variant_id = Column(UUID(as_uuid=True), ForeignKey("product_variants.id"), nullable=True)

    qty_on_hand = Column(Numeric(18, 4), default=0)     # Disponible
    qty_reserved = Column(Numeric(18, 4), default=0)    # Reservado (pedidos pendientes)
    qty_incoming = Column(Numeric(18, 4), default=0)    # En tránsito (compras / traslados)

    avg_cost = Column(Numeric(18, 4), default=0)        # Costo promedio ponderado

    # Relationships
    product = relationship("Product")
    warehouse = relationship("Warehouse")

    __table_args__ = (
        Index("ix_stock_quants_unique", "product_id", "warehouse_id", "variant_id", unique=True),
    )

    @property
    def qty_available(self):
        return self.qty_on_hand - self.qty_reserved


class PhysicalInventory(BaseModel):
    """Inventario físico / conteo cíclico."""
    __tablename__ = "physical_inventories"

    warehouse_id = Column(UUID(as_uuid=True), ForeignKey("warehouses.id"), nullable=False)
    name = Column(String(200), nullable=False)
    state = Column(String(20), default="draft")  # draft, in_progress, done, cancelled
    notes = Column(Text)
    done_at = Column(DateTime(timezone=True))
    user_id = Column(UUID(as_uuid=True), ForeignKey("users.id"))

    # Relationships
    lines = relationship("PhysicalInventoryLine", back_populates="inventory", lazy="selectin")


class PhysicalInventoryLine(BaseModel):
    """Línea de inventario físico por producto."""
    __tablename__ = "physical_inventory_lines"

    inventory_id = Column(UUID(as_uuid=True), ForeignKey("physical_inventories.id"), nullable=False)
    product_id = Column(UUID(as_uuid=True), ForeignKey("products.id"), nullable=False)
    lot_id = Column(UUID(as_uuid=True), ForeignKey("stock_lots.id"), nullable=True)

    qty_system = Column(Numeric(18, 4), default=0)   # Stock en sistema
    qty_counted = Column(Numeric(18, 4), default=0)  # Stock contado físicamente
    qty_diff = Column(Numeric(18, 4), default=0)     # Diferencia (counted - system)
    unit_cost = Column(Numeric(18, 4), default=0)

    # Relationships
    inventory = relationship("PhysicalInventory", back_populates="lines")
    product = relationship("Product")
