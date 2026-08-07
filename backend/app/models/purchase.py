"""Modelos: Órdenes de Compra y Recepción."""
from sqlalchemy import (
    Column, String, Boolean, ForeignKey, Text, Numeric, Enum as SAEnum,
    DateTime, Integer, Index
)
from sqlalchemy.dialects.postgresql import UUID, JSONB
from sqlalchemy.orm import relationship
import enum
from app.models.base import BaseModel


class PurchaseState(str, enum.Enum):
    DRAFT = "draft"
    CONFIRMED = "confirmed"
    RECEIVED = "received"
    PARTIAL = "partial"   # Recibido parcialmente
    CANCELLED = "cancelled"
    DONE = "done"


class PurchaseOrder(BaseModel):
    """Orden de compra."""
    __tablename__ = "purchase_orders"

    company_id = Column(UUID(as_uuid=True), ForeignKey("companies.id"), nullable=False)
    branch_id = Column(UUID(as_uuid=True), ForeignKey("branches.id"), nullable=False)
    warehouse_id = Column(UUID(as_uuid=True), ForeignKey("warehouses.id"), nullable=False)
    supplier_id = Column(UUID(as_uuid=True), ForeignKey("partners.id"), nullable=False)
    user_id = Column(UUID(as_uuid=True), ForeignKey("users.id"), nullable=False)

    number = Column(String(50), unique=True, nullable=False, index=True)
    supplier_ref = Column(String(200))  # Número del proveedor
    state = Column(SAEnum(PurchaseState), default=PurchaseState.DRAFT)

    order_date = Column(DateTime(timezone=True), nullable=False)
    expected_date = Column(DateTime(timezone=True))
    received_date = Column(DateTime(timezone=True))

    # Montos
    subtotal = Column(Numeric(18, 4), default=0)
    tax_amount = Column(Numeric(18, 4), default=0)
    discount_amount = Column(Numeric(18, 4), default=0)
    total = Column(Numeric(18, 4), default=0)

    notes = Column(Text)
    payment_terms = Column(String(200))

    # Relationships
    lines = relationship("PurchaseOrderLine", back_populates="purchase", lazy="selectin", cascade="all, delete-orphan")
    supplier = relationship("Partner", back_populates="purchases", foreign_keys=[supplier_id])
    receipts = relationship("GoodsReceipt", back_populates="purchase")

    __table_args__ = (
        Index("ix_purchase_orders_branch_date", "branch_id", "order_date"),
    )

    def __repr__(self):
        return f"<PurchaseOrder {self.number}>"


class PurchaseOrderLine(BaseModel):
    """Línea de orden de compra."""
    __tablename__ = "purchase_order_lines"

    purchase_id = Column(UUID(as_uuid=True), ForeignKey("purchase_orders.id"), nullable=False)
    product_id = Column(UUID(as_uuid=True), ForeignKey("products.id"), nullable=False)
    variant_id = Column(UUID(as_uuid=True), ForeignKey("product_variants.id"), nullable=True)

    product_name = Column(String(500), nullable=False)
    description = Column(Text)

    qty_ordered = Column(Numeric(18, 4), nullable=False, default=1)
    qty_received = Column(Numeric(18, 4), default=0)
    qty_pending = Column(Numeric(18, 4), default=0)

    unit_price = Column(Numeric(18, 4), nullable=False, default=0)
    tax_rate = Column(Numeric(5, 2), default=12.00)
    subtotal = Column(Numeric(18, 4), default=0)
    tax_amount = Column(Numeric(18, 4), default=0)
    total = Column(Numeric(18, 4), default=0)

    # Relationships
    purchase = relationship("PurchaseOrder", back_populates="lines")
    product = relationship("Product")


class GoodsReceipt(BaseModel):
    """Recepción de mercadería (puede ser parcial)."""
    __tablename__ = "goods_receipts"

    purchase_id = Column(UUID(as_uuid=True), ForeignKey("purchase_orders.id"), nullable=False)
    warehouse_id = Column(UUID(as_uuid=True), ForeignKey("warehouses.id"), nullable=False)
    user_id = Column(UUID(as_uuid=True), ForeignKey("users.id"), nullable=False)

    number = Column(String(50), unique=True, nullable=False)
    received_date = Column(DateTime(timezone=True), nullable=False)
    notes = Column(Text)
    state = Column(String(20), default="done")  # done, cancelled

    # Relationships
    purchase = relationship("PurchaseOrder", back_populates="receipts")
    lines = relationship("GoodsReceiptLine", back_populates="receipt", lazy="selectin", cascade="all, delete-orphan")


class GoodsReceiptLine(BaseModel):
    """Línea de recepción de mercadería."""
    __tablename__ = "goods_receipt_lines"

    receipt_id = Column(UUID(as_uuid=True), ForeignKey("goods_receipts.id"), nullable=False)
    purchase_line_id = Column(UUID(as_uuid=True), ForeignKey("purchase_order_lines.id"), nullable=False)
    product_id = Column(UUID(as_uuid=True), ForeignKey("products.id"), nullable=False)
    lot_id = Column(UUID(as_uuid=True), ForeignKey("stock_lots.id"), nullable=True)

    qty_received = Column(Numeric(18, 4), nullable=False)
    unit_cost = Column(Numeric(18, 4), nullable=False)

    # Relationships
    receipt = relationship("GoodsReceipt", back_populates="lines")
    product = relationship("Product")
