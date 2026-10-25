"""Modelos: Clientes, Proveedores (Socios/Partners)."""
from sqlalchemy import Column, String, Boolean, ForeignKey, Text, Numeric, Enum as SAEnum, Integer
from sqlalchemy.dialects.postgresql import UUID, JSONB
from sqlalchemy.orm import relationship
import enum
from app.models.base import BaseModel


class PartnerType(str, enum.Enum):
    CUSTOMER = "customer"
    SUPPLIER = "supplier"
    BOTH = "both"
    EMPLOYEE = "employee"


class CreditStatus(str, enum.Enum):
    NONE = "none"
    ACTIVE = "active"
    SUSPENDED = "suspended"
    BLOCKED = "blocked"


class Partner(BaseModel):
    """Partner: cliente y/o proveedor."""
    __tablename__ = "partners"

    company_id = Column(UUID(as_uuid=True), ForeignKey("companies.id"), nullable=False)
    partner_type = Column(SAEnum(PartnerType), default=PartnerType.CUSTOMER)

    # Identificación (Guatemala)
    code = Column(Integer, index=True, nullable=True)  # ID numérico correlativo (1, 2, 3...)
    nit = Column(String(50), index=True)  # NIT o CF (consumidor final)
    cui = Column(String(50))              # DPI
    name = Column(String(300), nullable=False, index=True)
    trade_name = Column(String(300))
    is_company = Column(Boolean, default=False)

    # Contacto
    email = Column(String(200))
    phone = Column(String(100))
    mobile = Column(String(100))
    address = Column(Text)
    city = Column(String(100))
    country = Column(String(100), default="Guatemala")

    # Crédito (clientes)
    credit_status = Column(SAEnum(CreditStatus), default=CreditStatus.NONE)
    credit_limit = Column(Numeric(18, 4), default=0)
    payment_days = Column(Integer, default=0)  # Días de crédito
    balance = Column(Numeric(18, 4), default=0)  # Saldo actual (+ = debe)

    # Precio especial
    price_list_id = Column(UUID(as_uuid=True), ForeignKey("price_lists.id"), nullable=True)

    # Proveedor
    bank_account = Column(String(150))
    bank_name = Column(String(255))

    # Foto / Logo del contacto
    image_url = Column(Text, nullable=True)

    is_active = Column(Boolean, default=True)
    notes = Column(Text)
    tags = Column(JSONB, default=list)

    # Relationships
    price_list = relationship("PriceList")
    sales = relationship("SaleOrder", back_populates="customer", foreign_keys="SaleOrder.customer_id")
    purchases = relationship("PurchaseOrder", back_populates="supplier", foreign_keys="PurchaseOrder.supplier_id")

    def __repr__(self):
        return f"<Partner {self.nit} - {self.name}>"
