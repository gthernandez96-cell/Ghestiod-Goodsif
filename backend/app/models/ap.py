"""Modelos: Cuentas por Pagar (AP) y Facturas de Proveedores."""
from sqlalchemy import Column, String, ForeignKey, Numeric, DateTime, Text
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.orm import relationship
from datetime import datetime, timezone
from app.models.base import BaseModel


class SupplierBill(BaseModel):
    """Factura de Proveedor para control de Cuentas por Pagar."""
    __tablename__ = "supplier_bills"

    company_id = Column(UUID(as_uuid=True), ForeignKey("companies.id"), nullable=False)
    supplier_id = Column(UUID(as_uuid=True), ForeignKey("partners.id"), nullable=False)
    number = Column(String(100), nullable=False, index=True)
    bill_date = Column(DateTime(timezone=True), default=lambda: datetime.now(timezone.utc))
    due_date = Column(DateTime(timezone=True))
    total = Column(Numeric(18, 4), nullable=False, default=0.0)
    balance = Column(Numeric(18, 4), nullable=False, default=0.0)
    state = Column(String(20), default="open")  # open, paid, cancelled
    notes = Column(Text, nullable=True)

    # Relationships
    supplier = relationship("Partner", foreign_keys=[supplier_id], lazy="selectin")
    payments = relationship("SupplierBillPayment", back_populates="bill", lazy="selectin", cascade="all, delete-orphan")

    def __repr__(self):
        return f"<SupplierBill {self.number} - Balance: {self.balance}>"


class SupplierBillPayment(BaseModel):
    """Registro de abonos y pagos a facturas de proveedores."""
    __tablename__ = "supplier_bill_payments"

    bill_id = Column(UUID(as_uuid=True), ForeignKey("supplier_bills.id"), nullable=False)
    amount = Column(Numeric(18, 4), nullable=False, default=0.0)
    payment_date = Column(DateTime(timezone=True), default=lambda: datetime.now(timezone.utc))
    payment_method = Column(String(50), default="transfer")  # cash, card, transfer, check
    reference = Column(String(100), nullable=True)
    notes = Column(Text, nullable=True)

    # Relationships
    bill = relationship("SupplierBill", back_populates="payments")
