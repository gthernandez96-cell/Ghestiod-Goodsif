"""Modelos: Órdenes de Venta y Facturación."""
from sqlalchemy import (
    Column, String, Boolean, ForeignKey, Text, Numeric, Enum as SAEnum,
    DateTime, Integer, Index
)
from sqlalchemy.dialects.postgresql import UUID, JSONB
from sqlalchemy.orm import relationship
import enum
from decimal import Decimal
from app.models.base import BaseModel


class SaleState(str, enum.Enum):
    DRAFT = "draft"
    PENDING_APPROVAL = "pending_approval"           # Solicitud POS / Pre-venta enviada
    WAREHOUSE_VALIDATION = "warehouse_validation"   # En validación con bodega
    APPROVED = "approved"                           # Autorizada (inventario rebajado)
    REJECTED = "rejected"                           # Rechazada
    CONFIRMED = "confirmed"
    INVOICED = "invoiced"
    PAID = "paid"
    CANCELLED = "cancelled"
    RETURNED = "returned"


class SaleType(str, enum.Enum):
    POS = "pos"           # Venta en mostrador
    PRESALE = "presale"   # Pre-venta (solicitud POS sujeta a autorización)
    QUOTE = "quote"       # Cotización
    ORDER = "order"       # Orden de venta
    CREDIT = "credit"     # Venta a crédito


class SaleOrder(BaseModel):
    """Orden de venta / Factura."""
    __tablename__ = "sale_orders"

    # Relaciones
    company_id = Column(UUID(as_uuid=True), ForeignKey("companies.id"), nullable=False)
    branch_id = Column(UUID(as_uuid=True), ForeignKey("branches.id"), nullable=False)
    warehouse_id = Column(UUID(as_uuid=True), ForeignKey("warehouses.id"), nullable=False)
    customer_id = Column(UUID(as_uuid=True), ForeignKey("partners.id"), nullable=True)
    cashier_session_id = Column(UUID(as_uuid=True), ForeignKey("cashier_sessions.id"), nullable=True)
    user_id = Column(UUID(as_uuid=True), ForeignKey("users.id"), nullable=False)
    price_list_id = Column(UUID(as_uuid=True), ForeignKey("price_lists.id"), nullable=True)

    # Aprobación y Validación (Pre-venta / Solicitudes POS)
    approved_by_id = Column(UUID(as_uuid=True), ForeignKey("users.id"), nullable=True)
    approved_at = Column(DateTime(timezone=True), nullable=True)
    rejection_reason = Column(Text, nullable=True)
    warehouse_verified = Column(Boolean, default=False)

    # Identificación
    number = Column(String(50), unique=True, nullable=False, index=True)
    state = Column(SAEnum(SaleState), default=SaleState.DRAFT)
    sale_type = Column(SAEnum(SaleType), default=SaleType.POS)

    # Fechas
    sale_date = Column(DateTime(timezone=True), nullable=False)
    due_date = Column(DateTime(timezone=True))

    # Cliente
    customer_nit = Column(String(20))   # NIT para FEL
    customer_name = Column(String(300)) # Nombre para FEL
    customer_address = Column(Text)

    # Montos
    subtotal = Column(Numeric(18, 4), default=0)      # Antes de impuesto
    tax_amount = Column(Numeric(18, 4), default=0)    # IVA
    discount_amount = Column(Numeric(18, 4), default=0)
    total = Column(Numeric(18, 4), default=0)         # Total con impuesto
    amount_paid = Column(Numeric(18, 4), default=0)
    amount_due = Column(Numeric(18, 4), default=0)

    # FEL
    fel_uuid = Column(String(200), index=True)        # UUID del DTE certificado
    fel_number = Column(String(200))                  # Número de autorización
    fel_series = Column(String(50))                   # Serie
    fel_certified_at = Column(DateTime(timezone=True))
    fel_status = Column(String(50))                   # pending, certified, cancelled
    fel_qr_url = Column(String(500))
    fel_pdf_url = Column(String(500))
    fel_xml = Column(Text)                            # XML del DTE

    # Notas
    notes = Column(Text)
    internal_notes = Column(Text)

    # Datos extra (descuentos globales, referencias)
    extra = Column(JSONB, default=dict)

    # Relationships
    lines = relationship("SaleOrderLine", back_populates="sale", lazy="selectin", cascade="all, delete-orphan")
    payments = relationship("SalePayment", back_populates="sale", lazy="selectin", cascade="all, delete-orphan")
    customer = relationship("Partner", back_populates="sales", foreign_keys=[customer_id])
    cashier_session = relationship("CashierSession", back_populates="sales")
    branch = relationship("Branch")
    warehouse = relationship("Warehouse", foreign_keys=[warehouse_id], lazy="selectin")
    user = relationship("User", foreign_keys=[user_id], lazy="selectin")
    approver = relationship("User", foreign_keys=[approved_by_id], lazy="selectin")

    __table_args__ = (
        Index("ix_sale_orders_branch_date", "branch_id", "sale_date"),
        Index("ix_sale_orders_state", "state"),
    )

    def __repr__(self):
        return f"<SaleOrder {self.number} - Q{self.total}>"


class SaleOrderLine(BaseModel):
    """Línea de orden de venta."""
    __tablename__ = "sale_order_lines"

    sale_id = Column(UUID(as_uuid=True), ForeignKey("sale_orders.id"), nullable=False)
    product_id = Column(UUID(as_uuid=True), ForeignKey("products.id"), nullable=False)
    variant_id = Column(UUID(as_uuid=True), ForeignKey("product_variants.id"), nullable=True)
    lot_id = Column(UUID(as_uuid=True), ForeignKey("stock_lots.id"), nullable=True)
    uom_id = Column(UUID(as_uuid=True), ForeignKey("units_of_measure.id"), nullable=True)
    serial_number = Column(String(100), nullable=True)

    # Producto
    product_name = Column(String(500), nullable=False)  # Snapshot del nombre
    product_barcode = Column(String(100))
    description = Column(Text)

    # Cantidades
    qty = Column(Numeric(18, 4), nullable=False, default=1)
    qty_returned = Column(Numeric(18, 4), default=0)

    # Precios
    unit_price = Column(Numeric(18, 4), nullable=False)
    cost_price = Column(Numeric(18, 4), default=0)      # Para cálculo de margen
    discount_pct = Column(Numeric(5, 2), default=0)     # % descuento
    discount_amount = Column(Numeric(18, 4), default=0)
    tax_rate = Column(Numeric(5, 2), default=12.00)
    subtotal = Column(Numeric(18, 4), default=0)
    tax_amount = Column(Numeric(18, 4), default=0)
    total = Column(Numeric(18, 4), default=0)

    # Relationships
    sale = relationship("SaleOrder", back_populates="lines")
    product = relationship("Product")

    def compute_totals(self, includes_tax: bool = True):
        """Recalcular totales de la línea respetando si el precio unitario ya incluye impuestos (IVA)."""
        FOUR = Decimal("0.0001")
        qty_dec = Decimal(str(self.qty or 0))
        price_dec = Decimal(str(self.unit_price or 0))
        disc_pct_dec = Decimal(str(self.discount_pct or 0))
        tax_rate_dec = Decimal(str(self.tax_rate or 0))

        gross = qty_dec * price_dec
        disc = gross * (disc_pct_dec / Decimal("100"))
        self.discount_amount = disc.quantize(FOUR)

        if includes_tax:
            # En Guatemala (SAT FEL), los precios de venta en mostrador/catálogo incluyen el 12% de IVA
            line_net = gross - disc
            tax_factor = Decimal("1") + (tax_rate_dec / Decimal("100"))
            if tax_factor > 0:
                self.subtotal = (line_net / tax_factor).quantize(FOUR)
            else:
                self.subtotal = line_net.quantize(FOUR)
            self.tax_amount = (line_net - self.subtotal).quantize(FOUR)
            self.total = line_net.quantize(FOUR)
        else:
            self.subtotal = (gross - disc).quantize(FOUR)
            self.tax_amount = (self.subtotal * (tax_rate_dec / Decimal("100"))).quantize(FOUR)
            self.total = (self.subtotal + self.tax_amount).quantize(FOUR)


    def __repr__(self):
        return f"<SaleLine {self.product_name} x{self.qty}>"


class SalePayment(BaseModel):
    """Pago de una venta (puede ser mixto)."""
    __tablename__ = "sale_payments"

    sale_id = Column(UUID(as_uuid=True), ForeignKey("sale_orders.id"), nullable=False)
    session_id = Column(UUID(as_uuid=True), ForeignKey("cashier_sessions.id"), nullable=True)

    payment_method = Column(String(30), nullable=False)  # cash, card, transfer, qr, credit
    amount = Column(Numeric(18, 4), nullable=False)
    reference = Column(String(200))    # Referencia tarjeta, # transferencia
    card_last4 = Column(String(4))
    notes = Column(Text)

    # Relationships
    sale = relationship("SaleOrder", back_populates="payments")


class POSSession(BaseModel):
    """Sesión POS para sincronización offline."""
    __tablename__ = "pos_sessions"

    branch_id = Column(UUID(as_uuid=True), ForeignKey("branches.id"), nullable=False)
    user_id = Column(UUID(as_uuid=True), ForeignKey("users.id"), nullable=False)
    device_id = Column(String(200))   # ID del dispositivo/browser
    state = Column(String(20), default="active")  # active, syncing, synced

    # Ventas offline pendientes de sincronizar
    offline_sales = Column(JSONB, default=list)
    last_sync_at = Column(DateTime(timezone=True))

    # Relationships
    branch = relationship("Branch", back_populates="pos_sessions")
