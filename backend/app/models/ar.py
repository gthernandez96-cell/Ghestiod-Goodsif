"""Modelos: Cuentas por Cobrar (AR) y Créditos a Clientes."""
from sqlalchemy import Column, String, ForeignKey, Numeric, DateTime, Text
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.orm import relationship
from datetime import datetime, timezone
from app.models.base import BaseModel


class CustomerInvoiceCredit(BaseModel):
    """Cuenta por Cobrar vinculada a una Venta o Factura a Crédito."""
    __tablename__ = "customer_invoice_credits"

    company_id = Column(UUID(as_uuid=True), ForeignKey("companies.id"), nullable=False)
    customer_id = Column(UUID(as_uuid=True), ForeignKey("partners.id"), nullable=True)
    sale_id = Column(UUID(as_uuid=True), ForeignKey("sale_orders.id"), nullable=True)
    number = Column(String(100), nullable=False, index=True)
    issue_date = Column(DateTime(timezone=True), default=lambda: datetime.now(timezone.utc))
    due_date = Column(DateTime(timezone=True))
    total = Column(Numeric(18, 4), nullable=False, default=0.0)
    balance = Column(Numeric(18, 4), nullable=False, default=0.0)
    state = Column(String(20), default="open")  # open, paid, cancelled, overdue
    notes = Column(Text, nullable=True)

    # Relationships
    customer = relationship("Partner", foreign_keys=[customer_id], lazy="selectin")
    sale = relationship("SaleOrder", foreign_keys=[sale_id], lazy="selectin")
    payments = relationship("CustomerCreditPayment", back_populates="invoice", lazy="selectin", cascade="all, delete-orphan")

    def __repr__(self):
        return f"<CustomerInvoiceCredit {self.number} - Cliente: {self.customer_id} - Saldo: {self.balance}>"


class CustomerCreditPayment(BaseModel):
    """Registro de abonos y pagos recibidos de clientes."""
    __tablename__ = "customer_credit_payments"

    company_id = Column(UUID(as_uuid=True), ForeignKey("companies.id"), nullable=False)
    invoice_id = Column(UUID(as_uuid=True), ForeignKey("customer_invoice_credits.id"), nullable=True)
    customer_id = Column(UUID(as_uuid=True), ForeignKey("partners.id"), nullable=True)
    receipt_number = Column(String(100), nullable=False, index=True)
    amount = Column(Numeric(18, 4), nullable=False, default=0.0)
    payment_date = Column(DateTime(timezone=True), default=lambda: datetime.now(timezone.utc))
    payment_method = Column(String(50), default="cash")  # cash, card, transfer, check
    reference = Column(String(100), nullable=True)
    notes = Column(Text, nullable=True)

    # Relationships
    invoice = relationship("CustomerInvoiceCredit", back_populates="payments")
    customer = relationship("Partner", foreign_keys=[customer_id], lazy="selectin")

    def __repr__(self):
        return f"<CustomerCreditPayment {self.receipt_number} - Monto: {self.amount}>"
