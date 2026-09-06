"""Modelos: Traslados internos entre sucursales."""
from sqlalchemy import Column, String, Boolean, ForeignKey, Text, Numeric, Enum as SAEnum, DateTime
from sqlalchemy.dialects.postgresql import UUID, JSONB
from sqlalchemy.orm import relationship
import enum
from app.models.base import BaseModel


class TransferState(str, enum.Enum):
    DRAFT = "draft"
    CONFIRMED = "confirmed"
    SENT = "sent"           # Despacho confirmado
    IN_TRANSIT = "in_transit"
    RECEIVED = "received"   # Completado
    PARTIAL = "partial"     # Recibido parcialmente
    CANCELLED = "cancelled"


class Transfer(BaseModel):
    """Traslado de mercadería entre sucursales/bodegas."""
    __tablename__ = "transfers"

    company_id = Column(UUID(as_uuid=True), ForeignKey("companies.id"), nullable=False)

    # Origen
    origin_branch_id = Column(UUID(as_uuid=True), ForeignKey("branches.id"), nullable=False)
    origin_warehouse_id = Column(UUID(as_uuid=True), ForeignKey("warehouses.id"), nullable=False)

    # Destino
    dest_branch_id = Column(UUID(as_uuid=True), ForeignKey("branches.id"), nullable=False)
    dest_warehouse_id = Column(UUID(as_uuid=True), ForeignKey("warehouses.id"), nullable=False)

    number = Column(String(50), unique=True, nullable=False, index=True)
    state = Column(SAEnum(TransferState), default=TransferState.DRAFT)

    # Fechas
    request_date = Column(DateTime(timezone=True))
    send_date = Column(DateTime(timezone=True))
    receive_date = Column(DateTime(timezone=True))

    # Usuarios
    requested_by = Column(UUID(as_uuid=True), ForeignKey("users.id"))
    approved_by = Column(UUID(as_uuid=True), ForeignKey("users.id"))
    sent_by = Column(UUID(as_uuid=True), ForeignKey("users.id"))
    received_by = Column(UUID(as_uuid=True), ForeignKey("users.id"))

    notes = Column(Text)
    rejection_reason = Column(Text)

    # Relationships
    lines = relationship("TransferLine", back_populates="transfer", lazy="selectin", cascade="all, delete-orphan")
    origin_branch = relationship("Branch", foreign_keys=[origin_branch_id], lazy="selectin")
    dest_branch = relationship("Branch", foreign_keys=[dest_branch_id], lazy="selectin")
    origin_warehouse = relationship("Warehouse", foreign_keys=[origin_warehouse_id], lazy="selectin")
    dest_warehouse = relationship("Warehouse", foreign_keys=[dest_warehouse_id], lazy="selectin")

    def __repr__(self):
        return f"<Transfer {self.number} {self.state}>"


class TransferLine(BaseModel):
    """Línea de traslado."""
    __tablename__ = "transfer_lines"

    transfer_id = Column(UUID(as_uuid=True), ForeignKey("transfers.id"), nullable=False)
    product_id = Column(UUID(as_uuid=True), ForeignKey("products.id"), nullable=False)
    variant_id = Column(UUID(as_uuid=True), ForeignKey("product_variants.id"), nullable=True)
    lot_id = Column(UUID(as_uuid=True), ForeignKey("stock_lots.id"), nullable=True)

    product_name = Column(String(500), nullable=False)

    qty_requested = Column(Numeric(18, 4), nullable=False)   # Cantidad solicitada
    qty_sent = Column(Numeric(18, 4), default=0)             # Cantidad enviada
    qty_received = Column(Numeric(18, 4), default=0)         # Cantidad recibida
    qty_diff = Column(Numeric(18, 4), default=0)             # Diferencia

    unit_cost = Column(Numeric(18, 4), default=0)
    notes = Column(Text)  # Notas de diferencia

    # Relationships
    transfer = relationship("Transfer", back_populates="lines")
    product = relationship("Product")

    def __repr__(self):
        return f"<TransferLine {self.product_name} x{self.qty_requested}>"
