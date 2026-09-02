"""Modelos: Contabilidad y Asientos Contables."""
from sqlalchemy import Column, String, ForeignKey, Numeric, DateTime
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.orm import relationship
from app.models.base import BaseModel


class Account(BaseModel):
    """Cuenta contable dentro del Catálogo de Cuentas."""
    __tablename__ = "accounts"

    company_id = Column(UUID(as_uuid=True), ForeignKey("companies.id"), nullable=False)
    code = Column(String(50), nullable=False, index=True)
    name = Column(String(200), nullable=False)
    account_type = Column(String(50))  # asset, liability, equity, income, expense

    def __repr__(self):
        return f"<Account {self.code} - {self.name}>"


class JournalEntry(BaseModel):
    """Partida / Asiento Contable."""
    __tablename__ = "journal_entries"

    company_id = Column(UUID(as_uuid=True), ForeignKey("companies.id"), nullable=False)
    number = Column(String(50), nullable=False, unique=True, index=True)
    journal = Column(String(50), nullable=False)  # Ventas, Compras, Operaciones Varias
    concept = Column(String(500), nullable=False)
    date = Column(DateTime(timezone=True), nullable=False)
    state = Column(String(20), default="posted")  # draft, posted

    # Relationships
    lines = relationship("JournalEntryLine", back_populates="entry", cascade="all, delete-orphan", lazy="selectin")

    def __repr__(self):
        return f"<JournalEntry {self.number} - {self.concept}>"


class JournalEntryLine(BaseModel):
    """Línea de detalle contable (Apunte contable)."""
    __tablename__ = "journal_entry_lines"

    entry_id = Column(UUID(as_uuid=True), ForeignKey("journal_entries.id"), nullable=False)
    account_id = Column(UUID(as_uuid=True), ForeignKey("accounts.id"), nullable=False)
    debit = Column(Numeric(18, 4), default=0.0)
    credit = Column(Numeric(18, 4), default=0.0)

    # Relationships
    entry = relationship("JournalEntry", back_populates="lines")
    account = relationship("Account")

    def __repr__(self):
        return f"<JournalEntryLine account_id: {self.account_id} Dr: {self.debit} Cr: {self.credit}>"
