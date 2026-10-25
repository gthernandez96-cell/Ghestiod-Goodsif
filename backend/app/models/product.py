"""Modelos: Productos, Categorías, Precios, Variantes."""
from sqlalchemy import (
    Column, String, Boolean, Integer, ForeignKey, Text, Numeric,
    Enum as SAEnum, UniqueConstraint, Index
)
from sqlalchemy.dialects.postgresql import UUID, JSONB, ARRAY
from sqlalchemy.orm import relationship, backref
import enum
from app.models.base import BaseModel


class ProductType(str, enum.Enum):
    PRODUCT = "product"       # Producto físico con inventario
    SERVICE = "service"       # Servicio (sin inventario)
    CONSUMABLE = "consumable" # Consumible (sin control de stock)


class TrackingMethod(str, enum.Enum):
    NONE = "none"             # Sin seguimiento
    LOT = "lot"               # Por lote (farmacia: vencimiento)
    SERIAL = "serial"         # Por número de serie (ferretería)


class ProductCategory(BaseModel):
    """Categoría de productos con jerarquía."""
    __tablename__ = "product_categories"

    company_id = Column(UUID(as_uuid=True), ForeignKey("companies.id"), nullable=False)
    parent_id = Column(UUID(as_uuid=True), ForeignKey("product_categories.id"), nullable=True)
    name = Column(String(200), nullable=False)
    code = Column(String(50))
    description = Column(Text)
    image_url = Column(String(500))
    is_active = Column(Boolean, default=True)

    # Contabilidad / impuestos por categoría
    tax_rate = Column(Numeric(5, 2), default=12.00)  # % IVA por defecto
    cost_method = Column(String(20), default="average")  # average, fifo, lifo

    # Relationships
    children = relationship(
        "ProductCategory",
        backref=backref("parent", remote_side="ProductCategory.id"),
        lazy="selectin",
        foreign_keys=[parent_id]
    )
    products = relationship("Product", back_populates="category")

    def __repr__(self):
        return f"<Category {self.name}>"


class UnitOfMeasure(BaseModel):
    """Unidades de medida y conversiones."""
    __tablename__ = "units_of_measure"

    company_id = Column(UUID(as_uuid=True), ForeignKey("companies.id"), nullable=False)
    name = Column(String(100), nullable=False)
    symbol = Column(String(20), nullable=False)  # kg, lb, pz, caja, docena
    category = Column(String(50))  # weight, volume, unit, length
    ratio = Column(Numeric(18, 6), default=1.0)  # Factor de conversión respecto a unidad base
    is_base = Column(Boolean, default=False)
    is_active = Column(Boolean, default=True)

    def __repr__(self):
        return f"<UOM {self.symbol}>"


class Product(BaseModel):
    """Producto maestro."""
    __tablename__ = "products"

    company_id = Column(UUID(as_uuid=True), ForeignKey("companies.id"), nullable=False)
    category_id = Column(UUID(as_uuid=True), ForeignKey("product_categories.id"), nullable=True)
    uom_id = Column(UUID(as_uuid=True), ForeignKey("units_of_measure.id"), nullable=True)
    uom_purchase_id = Column(UUID(as_uuid=True), ForeignKey("units_of_measure.id"), nullable=True)

    # Identificación
    name = Column(String(500), nullable=False, index=True)
    internal_ref = Column(String(100), index=True)  # Código interno
    barcode = Column(String(100), index=True)        # EAN-13 / código de barras
    description = Column(Text)
    description_sale = Column(Text)
    description_purchase = Column(Text)
    internal_notes = Column(Text)  # Notas / Observaciones para POS y Preventa
    image_url = Column(String(500))
    pdf_datasheet_url = Column(String(500))  # Ficha técnica en PDF

    # Tipo y seguimiento
    product_type = Column(SAEnum(ProductType), default=ProductType.PRODUCT)
    tracking = Column(SAEnum(TrackingMethod), default=TrackingMethod.NONE)

    # Precios base
    sale_price = Column(Numeric(18, 4), nullable=False, default=0)
    cost_price = Column(Numeric(18, 4), default=0)
    min_price = Column(Numeric(18, 4), default=0)  # Precio mínimo de venta
    wholesale_price = Column(Numeric(18, 4), default=0)  # Precio de venta mayorista
    wholesale_uom_id = Column(UUID(as_uuid=True), ForeignKey("units_of_measure.id"), nullable=True)  # Unidad mayorista (paquete, caja, etc)
    wholesale_ratio = Column(Numeric(18, 4), default=1.0)  # Cantidad de unidades base que contiene (ej: 12 por paquete)

    # Impuestos
    tax_rate = Column(Numeric(5, 2), default=12.00)
    includes_tax = Column(Boolean, default=True)  # Precio incluye IVA

    # Inventario
    min_stock = Column(Numeric(18, 4), default=0)  # Stock mínimo (alerta)
    max_stock = Column(Numeric(18, 4), default=0)  # Stock máximo
    reorder_point = Column(Numeric(18, 4), default=0)

    # Estado
    is_active = Column(Boolean, default=True)
    is_published = Column(Boolean, default=True)  # Visible en POS
    can_be_sold = Column(Boolean, default=True)
    can_be_purchased = Column(Boolean, default=True)

    # Metadata extra
    supplier_id = Column(UUID(as_uuid=True), ForeignKey("partners.id"), nullable=True)
    attributes = Column(JSONB, default=dict)  # Atributos flexibles

    # Relationships
    category = relationship("ProductCategory", back_populates="products")
    supplier = relationship("Partner", foreign_keys=[supplier_id], lazy="selectin")
    uom = relationship("UnitOfMeasure", foreign_keys=[uom_id])
    uom_purchase = relationship("UnitOfMeasure", foreign_keys=[uom_purchase_id])
    wholesale_uom = relationship("UnitOfMeasure", foreign_keys=[wholesale_uom_id])
    variants = relationship("ProductVariant", back_populates="product", lazy="selectin")
    price_lists = relationship("ProductPrice", back_populates="product")
    stock_lots = relationship("StockLot", back_populates="product")
    suppliers = relationship("ProductSupplier", back_populates="product")

    __table_args__ = (
        Index("ix_products_company_active", "company_id", "is_active"),
    )

    def __repr__(self):
        return f"<Product {self.internal_ref} - {self.name}>"


class ProductVariant(BaseModel):
    """Variante de producto (talla S/M/L, color rojo/azul, etc.)."""
    __tablename__ = "product_variants"

    product_id = Column(UUID(as_uuid=True), ForeignKey("products.id"), nullable=False)
    name = Column(String(300), nullable=False)  # "Camisa Talla M Color Rojo"
    barcode = Column(String(100), index=True)
    internal_ref = Column(String(100))
    attributes = Column(JSONB, default=dict)  # {"talla": "M", "color": "rojo"}
    extra_price = Column(Numeric(18, 4), default=0)  # Diferencia de precio vs base
    is_active = Column(Boolean, default=True)

    # Relationships
    product = relationship("Product", back_populates="variants")

    def __repr__(self):
        return f"<Variant {self.name}>"


class PriceList(BaseModel):
    """Lista de precios (Mayorista, Minorista, Especial, etc.)."""
    __tablename__ = "price_lists"

    company_id = Column(UUID(as_uuid=True), ForeignKey("companies.id"), nullable=False)
    name = Column(String(200), nullable=False)
    code = Column(String(50))
    currency = Column(String(10), default="GTQ")
    discount_policy = Column(String(50), default="discount")  # discount | price
    is_active = Column(Boolean, default=True)
    is_default = Column(Boolean, default=False)

    # Relationships
    prices = relationship("ProductPrice", back_populates="price_list")

    def __repr__(self):
        return f"<PriceList {self.name}>"


class ProductPrice(BaseModel):
    """Precio específico de un producto en una lista de precios."""
    __tablename__ = "product_prices"

    product_id = Column(UUID(as_uuid=True), ForeignKey("products.id"), nullable=False)
    price_list_id = Column(UUID(as_uuid=True), ForeignKey("price_lists.id"), nullable=False)
    price = Column(Numeric(18, 4), nullable=False)
    min_qty = Column(Numeric(18, 4), default=0)  # Cantidad mínima para este precio
    date_start = Column(String(20))  # Vigencia inicio
    date_end = Column(String(20))    # Vigencia fin

    # Relationships
    product = relationship("Product", back_populates="price_lists")
    price_list = relationship("PriceList", back_populates="prices")

    __table_args__ = (
        UniqueConstraint("product_id", "price_list_id", "min_qty"),
    )


class ProductSupplier(BaseModel):
    """Proveedores de un producto con su precio de compra."""
    __tablename__ = "product_suppliers"

    product_id = Column(UUID(as_uuid=True), ForeignKey("products.id"), nullable=False)
    supplier_id = Column(UUID(as_uuid=True), ForeignKey("partners.id"), nullable=False)
    supplier_sku = Column(String(100))  # Código del proveedor
    price = Column(Numeric(18, 4), default=0)
    min_qty = Column(Numeric(18, 4), default=1)
    lead_time = Column(Integer, default=1)  # Días de entrega

    # Relationships
    product = relationship("Product", back_populates="suppliers")
