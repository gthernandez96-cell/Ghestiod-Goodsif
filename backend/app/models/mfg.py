"""Modelos: Fabricación, Recetas (BOM) y Órdenes de Producción."""
from sqlalchemy import Column, String, ForeignKey, Numeric, DateTime
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.orm import relationship
from app.models.base import BaseModel


class BOM(BaseModel):
    """BOM (Bill of Materials) - Lista de Materiales / Receta."""
    __tablename__ = "boms"

    company_id = Column(UUID(as_uuid=True), ForeignKey("companies.id"), nullable=False)
    product_id = Column(UUID(as_uuid=True), ForeignKey("products.id"), nullable=False, index=True)
    name = Column(String(200), nullable=False)

    # Relationships
    items = relationship("BOMItem", back_populates="bom", cascade="all, delete-orphan", lazy="selectin")
    product = relationship("Product", foreign_keys=[product_id])

    def __repr__(self):
        return f"<BOM {self.name} for Product {self.product_id}>"


class BOMItem(BaseModel):
    """Componente / ingrediente de una receta BOM."""
    __tablename__ = "bom_items"

    bom_id = Column(UUID(as_uuid=True), ForeignKey("boms.id"), nullable=False)
    product_id = Column(UUID(as_uuid=True), ForeignKey("products.id"), nullable=False)
    qty = Column(Numeric(18, 4), nullable=False, default=1.0)

    # Relationships
    bom = relationship("BOM", back_populates="items")
    product = relationship("Product", foreign_keys=[product_id])

    def __repr__(self):
        return f"<BOMItem product_id: {self.product_id} qty: {self.qty}>"


class ProductionOrder(BaseModel):
    """Orden de Producción / Horneado (Manufacturing Order)."""
    __tablename__ = "production_orders"

    company_id = Column(UUID(as_uuid=True), ForeignKey("companies.id"), nullable=False)
    number = Column(String(50), nullable=False, unique=True, index=True)
    product_id = Column(UUID(as_uuid=True), ForeignKey("products.id"), nullable=False)
    quantity = Column(Numeric(18, 4), nullable=False, default=1.0)
    state = Column(String(20), default="in_progress")  # in_progress, done
    date_planned = Column(DateTime(timezone=True))
    date_finished = Column(DateTime(timezone=True))

    # Relationships
    product = relationship("Product", foreign_keys=[product_id])

    def __repr__(self):
        return f"<ProductionOrder {self.number} - {self.state}>"
