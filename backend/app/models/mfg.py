"""Modelos: Fabricación, Recetas Favoritas y Órdenes de Fabricación (Multi-Bodega)."""
from sqlalchemy import Column, String, ForeignKey, Numeric, DateTime, Boolean, Text
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.orm import relationship
from datetime import datetime, timezone
from app.models.base import BaseModel


class RecetaFavorita(BaseModel):
    """Plantilla / Receta Favorita para ensamblaje y fabricación recurrente."""
    __tablename__ = "recetas_favoritas"

    company_id = Column(UUID(as_uuid=True), ForeignKey("companies.id"), nullable=False)
    nombre = Column(String(200), nullable=False)
    producto_final_id = Column(UUID(as_uuid=True), ForeignKey("products.id"), nullable=False, index=True)
    bodega_destino_id = Column(UUID(as_uuid=True), ForeignKey("warehouses.id"), nullable=True)  # Bodega de destino sugerida
    cantidad_producida = Column(Numeric(18, 4), nullable=False, default=1.0)
    precio_venta_sugerido = Column(Numeric(18, 4), default=0.0)
    costo_total_estimado = Column(Numeric(18, 4), default=0.0)
    is_active = Column(Boolean, default=True)

    # Relationships
    detalles = relationship("DetalleReceta", back_populates="receta", cascade="all, delete-orphan", lazy="selectin")
    producto_final = relationship("Product", foreign_keys=[producto_final_id], lazy="selectin")
    bodega_destino = relationship("Warehouse", foreign_keys=[bodega_destino_id], lazy="selectin")

    def __repr__(self):
        return f"<RecetaFavorita {self.nombre} -> Producto {self.producto_final_id}>"


class DetalleReceta(BaseModel):
    """Componente / insumo de una receta favorita con su bodega de origen sugerida."""
    __tablename__ = "detalle_recetas"

    receta_id = Column(UUID(as_uuid=True), ForeignKey("recetas_favoritas.id"), nullable=False)
    producto_id = Column(UUID(as_uuid=True), ForeignKey("products.id"), nullable=False)
    bodega_origen_id = Column(UUID(as_uuid=True), ForeignKey("warehouses.id"), nullable=True)  # Bodega origen sugerida
    cantidad = Column(Numeric(18, 4), nullable=False, default=1.0)

    # Relationships
    receta = relationship("RecetaFavorita", back_populates="detalles")
    producto = relationship("Product", foreign_keys=[producto_id], lazy="selectin")
    bodega_origen = relationship("Warehouse", foreign_keys=[bodega_origen_id], lazy="selectin")

    def __repr__(self):
        return f"<DetalleReceta prod: {self.producto_id} bodega: {self.bodega_origen_id} qty: {self.cantidad}>"


class OrdenFabricacion(BaseModel):
    """Registro histórico de ensamblaje / fabricación ejecutada."""
    __tablename__ = "ordenes_fabricacion"

    company_id = Column(UUID(as_uuid=True), ForeignKey("companies.id"), nullable=False)
    numero = Column(String(50), nullable=False, unique=True, index=True)
    producto_final_id = Column(UUID(as_uuid=True), ForeignKey("products.id"), nullable=False, index=True)
    bodega_destino_id = Column(UUID(as_uuid=True), ForeignKey("warehouses.id"), nullable=False, index=True)  # Obligatorio
    cantidad_producida = Column(Numeric(18, 4), nullable=False, default=1.0)
    costo_total = Column(Numeric(18, 4), nullable=False, default=0.0)
    costo_unitario = Column(Numeric(18, 4), nullable=False, default=0.0)
    precio_venta = Column(Numeric(18, 4), nullable=False, default=0.0)
    estado = Column(String(20), default="done")  # done, cancelled
    receta_id = Column(UUID(as_uuid=True), ForeignKey("recetas_favoritas.id"), nullable=True)
    user_id = Column(UUID(as_uuid=True), ForeignKey("users.id"), nullable=True)
    fecha = Column(DateTime(timezone=True), default=lambda: datetime.now(timezone.utc))
    notas = Column(Text, nullable=True)

    # Relationships
    detalles = relationship("DetalleFabricacion", back_populates="orden", cascade="all, delete-orphan", lazy="selectin")
    producto_final = relationship("Product", foreign_keys=[producto_final_id], lazy="selectin")
    bodega_destino = relationship("Warehouse", foreign_keys=[bodega_destino_id], lazy="selectin")
    receta = relationship("RecetaFavorita", foreign_keys=[receta_id], lazy="selectin")
    user = relationship("User", foreign_keys=[user_id], lazy="selectin")

    def __repr__(self):
        return f"<OrdenFabricacion {self.numero} ({self.producto_final_id}) -> Bodega {self.bodega_destino_id}>"


class DetalleFabricacion(BaseModel):
    """Insumo consumido en una fabricación con su bodega de origen específica."""
    __tablename__ = "detalle_fabricacion"

    orden_id = Column(UUID(as_uuid=True), ForeignKey("ordenes_fabricacion.id"), nullable=False)
    producto_id = Column(UUID(as_uuid=True), ForeignKey("products.id"), nullable=False)
    bodega_origen_id = Column(UUID(as_uuid=True), ForeignKey("warehouses.id"), nullable=False)  # Obligatorio por insumo
    cantidad = Column(Numeric(18, 4), nullable=False, default=1.0)
    costo_unitario = Column(Numeric(18, 4), nullable=False, default=0.0)
    costo_subtotal = Column(Numeric(18, 4), nullable=False, default=0.0)

    # Relationships
    orden = relationship("OrdenFabricacion", back_populates="detalles")
    producto = relationship("Product", foreign_keys=[producto_id], lazy="selectin")
    bodega_origen = relationship("Warehouse", foreign_keys=[bodega_origen_id], lazy="selectin")

    def __repr__(self):
        return f"<DetalleFabricacion {self.producto_id} de Bodega {self.bodega_origen_id} qty: {self.cantidad}>"


# Alias para retrocompatibilidad
BOM = RecetaFavorita
BOMItem = DetalleReceta
ProductionOrder = OrdenFabricacion
